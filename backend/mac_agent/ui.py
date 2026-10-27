"""Local browser screen for the Mac agent: `python -m mac_agent --ui`.

Serves one page on 127.0.0.1 that starts/stops a sync and shows live progress. The sync runs in a worker thread
(the agent uses blocking HTTP calls) and the page polls /api/state.
"""
import asyncio
import contextlib
import json
import os
import secrets
import threading
import traceback
import time
import webbrowser
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from app import thumbs
from app.config import DATA_ROOT
from app.device import DeviceFile, device_state, open_afc, scan

from . import scancache, videothumb
from .tls import explain as tls_hint, new_client
from .agent import (AuthError, Stopped, check_existing, day_after, day_start, delete_from_phone, ext_key,
                    extension_counts, parse_pairing, preflight, select, sync)
from .favorites import FavoritesUnavailable, fetch_favorites, path_key

CONFIG = Path.home() / ".iphone_mac_agent.json"
HOSTS = {"127.0.0.1", "localhost"}
SCAN_TTL = 600  # seconds a phone scan is reused before it is read again
REASONS = {
    "NoAppleService": "The USB service isn't reachable (this needs a Mac with the phone plugged in).",
    "NoDevice": "Plug in the iPhone with a cable.",
    "Locked": "Unlock the iPhone.",
    "NotTrusted": "Tap Trust on the iPhone and enter the passcode.",
}


def make_client(server: str, token: str) -> httpx.Client:
    return new_client(server, token)


def probe_server(server: str, token: str) -> tuple[bool, str]:
    """Quick reachability + token check (a few seconds at most), so the screen can switch off what needs the server."""
    if not server or not token:
        return False, "Enter the server address and token."
    try:
        with make_client(server, token) as c:
            r = c.get("/api/device", timeout=4)
        if r.status_code == 401:
            return False, "The server rejected the token."
        r.raise_for_status()
        return True, ""
    except httpx.HTTPError as e:
        return False, f"Cannot reach the server ({type(e).__name__}).{tls_hint(e)}"


class Params(BaseModel):
    server: str
    token: str
    dest: str = ""
    kinds: list[str] = []
    since: str = ""
    until: str = ""
    force: bool = False
    rescan: bool = False
    exclude: list[str] = []  # phone paths unticked in the preview
    include: list[str] | None = None  # if set, only these paths (the preview was "select none" then ticked)
    exts: list[str] | None = None  # if set, only these extensions (".heic"); None = every extension
    only_existing: bool = False  # copy only files the server already has (used with force, to replace them)
    confirm: str = ""  # must be "DELETE" for /api/delete ("DELETE UNVERIFIED" with skip_verify)
    skip_verify: bool = False  # delete without checking the server (only when the user chose so; Favourites still kept)


class ReplaceRequest(BaseModel):
    dest: str = ""


def _idle() -> dict:
    return {"phase": "idle", "message": "", "index": 0, "total": 0, "name": "", "file_size": 0, "file_bytes": 0,
            "total_bytes": 0, "bytes_sent": 0, "speed": 0.0, "eta": None, "uploaded": 0, "duplicates": 0,
            "skipped": 0, "failed": 0, "failures": [], "log": [], "matched": 0, "matched_bytes": 0,
            "scanned_at": None, "extensions": [], "existing_count": 0, "existing_checked": False,
            "skipped_available": 0, "deleted": 0, "kept": 0, "bytes_freed": 0, "kept_items": [], "op": "", "steps": [], "skipped_steps": [], "step": 0, "scan_count": 0}


class Job:
    """One scan or sync at a time; `state` is what the page shows. The phone scan is cached between runs."""

    def __init__(self):
        self.state = _idle()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.scan: list[DeviceFile] | None = None
        self.scan_kinds: set[str] | None = None  # what the cached scan covers (None = everything)
        self.scanned_at = 0.0
        self.by_path: dict[str, DeviceFile] = {}
        self.stems: dict[tuple[str, str], DeviceFile] = {}
        self.files: list[DeviceFile] = []  # the last preview, in display order
        self.existing: set[str] = set()  # phone paths of the last preview that the server already has
        self.last_params: Params | None = None  # the last copy, so its skipped files can be replaced
        self.skipped_paths: list[str] = []
        self.check_server = False  # whether the running preview asks the server which files it already has
        self.hashes: dict[str, str] = {}  # phone path -> sha256 of what this session sent (lets a delete skip re-reading)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, p: Params, preview: bool = False, delete: bool = False) -> None:
        with self._lock:
            if self.running:
                raise HTTPException(409, "A sync is already running")
            self._stop.clear()
            keep = {k: self.state[k] for k in ("matched", "matched_bytes", "scanned_at", "extensions", "existing_count", "existing_checked")} if not preview else {}
            keep["log"] = self.state["log"]
            steps = ["Check phone", "Read phone"] if preview else ["Check server", "Check phone", "Read phone",
                                                                    "Verify & delete" if delete else "Copy files"]
            self.check_server, skip_note, skipped = False, "", [0] if delete and p.skip_verify else []
            if preview:
                steps.append("Check server")  # which of these files the server already has; shown as skipped if it can't run
                if p.server and p.token:
                    self.check_server, why = probe_server(p.server, p.token)
                    if not self.check_server:
                        skip_note = f"{why} Skipping the check of which files the server already has."
                if not self.check_server:
                    skipped = [len(steps) - 1]
            if not preview and not delete:
                self.last_params, self.skipped_paths = p, []
            self.state = {**_idle(), **keep, "phase": "starting", "message": "Starting...",
                          "op": "preview" if preview else "delete" if delete else "copy", "steps": steps,
                          "skipped_steps": skipped}
            if skip_note:
                self._log(skip_note)
            self._thread = threading.Thread(target=lambda: asyncio.run(self._run(p, preview, delete)), daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _log(self, line: str) -> None:
        """Timestamped line for the page's Activity log and for the terminal the screen was started from."""
        full = f"{time.strftime('%H:%M:%S')}  {line}"
        self.state["log"] = (self.state["log"] + [full])[-500:]
        print(full, flush=True)

    def _progress(self, cur: dict) -> None:
        left = cur["total_bytes"] - cur["bytes_sent"]
        self.state.update(cur, phase="uploading", step=3, eta=left / cur["speed"] if cur["speed"] > 1 else None)

    def _found(self, n: int) -> None:
        if self._stop.is_set():
            raise Stopped()
        self.state["scan_count"] = n
        if n % 1000 == 0:
            self._log(f"Reading the phone... {n:,} files found so far")

    def _reindex(self) -> None:
        """By path, and by (folder, name without extension) so an .AAE edit file can find the photo it belongs to."""
        self.by_path = {f.path: f for f in self.scan or []}
        self.stems: dict[tuple[str, str], DeviceFile] = {}
        for f in self.scan or []:
            if f.ext == ".aae" or f.kind not in ("photos", "videos"):
                continue
            folder, _, name = f.path.rpartition("/")
            key = (folder, name.rsplit(".", 1)[0].lower())
            if key not in self.stems or f.kind == "photos":  # a Live Photo is a photo + a video: show the photo
                self.stems[key] = f

    def _covers(self, need: set[str] | None) -> bool:
        return self.scan_kinds is None or (need is not None and need <= self.scan_kinds)

    async def _selection(self, afc, udid: str, p: Params, step: int) -> list[DeviceFile]:
        """Phone files matching the filters, newest first, reading the phone only when the cached scan is stale."""
        need = set(p.kinds) or None
        if self.scan is None or p.rescan or not self._covers(need) or time.time() - self.scanned_at > SCAN_TTL:
            self.state.update(phase="scanning", step=step, scan_count=0, message="Reading the phone...")
            known = {} if p.rescan else scancache.load(udid)  # earlier scans: only new files cost a round trip
            self._log(f"Reading the phone ({'full rescan' if p.rescan else f'{len(known):,} entries remembered from earlier'})...")
            self.scan = await scan(afc, self._found, need, known)
            scancache.save(udid, known)
            self.scan_kinds = need
            self.scanned_at = time.time()
            self._log(f"Phone read: {len(self.scan):,} files found.")
            self._reindex()
        else:
            self.state.update(step=step, scan_count=len(self.scan), message="Using the phone scan from earlier...")
            self._log(f"Using the phone scan from earlier ({len(self.scan):,} files; press Rescan to read again).")
        self.state["scanned_at"] = self.scanned_at
        since = day_start(p.since) if p.since else None
        until = day_after(p.until) if p.until else None
        files = sorted(select(self.scan, set(p.kinds) or None, since, until), key=lambda f: f.mtime, reverse=True)
        if p.exts is not None:
            allowed = set(p.exts)
            files = [f for f in files if ext_key(f) in allowed]
        if p.include is not None:
            keep = set(p.include)
            files = [f for f in files if f.path in keep]
        if p.exclude:
            drop = set(p.exclude)
            files = [f for f in files if f.path not in drop]
        return files

    def _del_progress(self, cur: dict) -> None:
        self.state.update(cur, phase="deleting", step=3)

    def _forget(self, udid: str, gone: set[str]) -> None:
        """Deleted from the phone: drop them from the cached scan, the preview and the remembered facts."""
        if self.scan is not None:
            self.scan = [f for f in self.scan if f.path not in gone]
            self._reindex()
        self.files = [f for f in self.files if f.path not in gone]
        self.existing -= gone
        known = scancache.load(udid)
        for path in gone:
            known.pop(path, None)
        scancache.save(udid, known)

    async def _run(self, p: Params, preview: bool, delete: bool = False) -> None:
        st = self.state
        n = 0  # index of the step being worked on
        op = "DELETE" if delete else "PREVIEW" if preview else "COPY"
        when = " ".join(x for x in (f"from {p.since}" if p.since else "", f"to {p.until}" if p.until else "") if x)
        self._log(f"=== {op} started: {', '.join(p.kinds) or 'all kinds'}{' ' + when if when else ''} ===")
        try:
            with contextlib.ExitStack() as stack:
                client = None
                if delete and p.skip_verify:
                    n = 1
                    self._log("Server check skipped by choice: files will be deleted without verification.")
                elif not preview:  # looking at the phone needs no server
                    st.update(phase="starting", step=0, message=f"Checking the server at {p.server}...")
                    self._log(f"Checking the server at {p.server}...")
                    client = stack.enter_context(make_client(p.server, p.token))
                    preflight(client)
                    self._log("Server reachable, token accepted.")
                    n = 1
                st.update(phase="starting", step=n, message="Checking the phone...")
                self._log("Checking the phone...")
                phone = await device_state()
                if not phone["connected"]:
                    raise RuntimeError(REASONS.get(phone["reason"], phone["reason"]))
                self._log(f"Phone connected: {phone.get('name', 'iPhone')} (iOS {phone.get('ios', '?')}).")
                async with open_afc() as (udid, afc):
                    files = await self._selection(afc, udid, p.model_copy(update={"exts": None} if preview else {}), n + 1)
                    self._log(f"{len(files):,} files match the filters ({sum(f.size for f in files) / 1e6:,.1f} MB).")
                    if preview:
                        self.files = files
                        self.existing, checked = set(), False
                        note = ""
                        if p.server and p.token and self.check_server:
                            st.update(phase="checking", step=n + 2,
                                      message="Asking the server which of these it already has...")
                            try:
                                with make_client(p.server, p.token) as c:
                                    self.existing, checked = check_existing(c, udid, files), True
                            except (AuthError, httpx.HTTPError):
                                note = " (Could not check the server for existing copies.)"
                        st.update(phase="previewed", step=len(st["steps"]), matched=len(files),
                                  extensions=extension_counts(files), existing_count=len(self.existing),
                                  existing_checked=checked, matched_bytes=sum(f.size for f in files),
                                  message=f"{len(files)} files match.{note}")
                        self._log(f"Preview ready: {len(files):,} files"
                                  + (f", {len(self.existing):,} already on the server" if checked else "") + f".{note}")
                        return
                    if delete:
                        shown = {f.path for f in self.files}  # only what the preview listed, never more
                        files = [f for f in files if f.path in shown]
                        st.update(phase="favorites", step=3, message="Reading your Favourites from the phone's Photos database...")
                        self._log("Reading your Favourites from the phone's Photos database (about 300 MB, up to a minute)...")
                        try:
                            favorites = await fetch_favorites(afc)
                        except FavoritesUnavailable as e:
                            raise RuntimeError(f"Nothing was deleted: {e}. Favourites can't be protected without it.") from e
                        kept = sum(1 for f in files if path_key(f.path) in favorites)
                        self._log(f"{len(favorites):,} Favourites found; {kept:,} of the {len(files):,} selected files belong "
                                  "to them (with Live Photo videos, edit data and edited versions) and will be kept.")
                        st.update(phase="deleting", step=3, total=len(files),
                                  message=f"Verifying, then deleting {len(files)} files...")
                        d = await delete_from_phone(
                            client, afc, udid, files, favorites=favorites, hashes=self.hashes, log=self._log, verify=not p.skip_verify,
                            should_stop=self._stop.is_set, on_progress=self._del_progress,
                        )
                        if d.deleted_paths:
                            self._forget(udid, set(d.deleted_paths))
                        st.update(extensions=extension_counts(self.files), matched=len(self.files),
                                  existing_count=len(self.existing))  # the preview list shrank
                        st.update(failures=[list(x) for x in d.failed], kept_items=[list(x) for x in d.kept][:200],
                                  deleted=d.deleted, kept=len(d.kept), failed=len(d.failed), bytes_freed=d.bytes_freed,
                                  phase="stopped" if d.stopped else "done", step=len(st["steps"]),
                                  message="Stopped." if d.stopped else "Finished.")
                        self._log(f"=== DELETE {'stopped' if d.stopped else 'finished'}: {d.deleted} deleted, "
                                  f"{len(d.kept)} kept, {len(d.failed)} failed, {d.bytes_freed / 1e6:,.1f} MB freed ===")
                        return
                    if p.only_existing:  # replacing: just the files the server already has
                        have = check_existing(client, udid, files)
                        files = [f for f in files if f.path in have]
                    st.update(phase="uploading", step=3, total=len(files), total_bytes=sum(f.size for f in files),
                              message=f"Copying {len(files)} files...")
                    s = await sync(
                        client, afc, udid, destination=p.dest or None, force=p.force or p.only_existing, files=files,
                        log=self._log, should_stop=self._stop.is_set, on_progress=self._progress,
                    )
            self.skipped_paths = s.skipped_paths
            self.hashes.update(s.hashes)
            st["skipped_available"] = len(s.skipped_paths)
            st["failures"] = [list(x) for x in s.failed]
            st.update(uploaded=s.uploaded, duplicates=s.duplicates, skipped=s.skipped, failed=len(s.failed))
            st.update(phase="stopped" if s.stopped else "done", step=len(st["steps"]),
                      message="Stopped." if s.stopped else "Finished.")
            self._log(f"=== COPY {'stopped' if s.stopped else 'finished'}: {s.uploaded} uploaded, {s.duplicates} duplicates, "
                      f"{s.skipped} already there, {len(s.failed)} failed, {s.bytes_sent / 1e6:,.1f} MB sent ===")
        except Stopped:  # unticked mid-scan
            st.update(phase="stopped", message="Cancelled.")
            self._log("=== Cancelled ===")
        except AuthError as e:
            st.update(phase="error", message=str(e))
            self._log(f"ERROR: {e}")
        except httpx.HTTPError as e:
            st.update(phase="error", message=f"Cannot reach {p.server}: {e}{tls_hint(e)}")
            self._log(f"ERROR: cannot reach {p.server}: {e}{tls_hint(e)}")
        except Exception as e:  # noqa: BLE001 - show anything unexpected on the page
            for line in traceback.format_exc().splitlines()[-12:]:  # the Log section of the page shows where
                self._log(line)
            st.update(phase="error", message=f"{type(e).__name__}: {e}" if str(e) else type(e).__name__)
            self._log(f"ERROR: {st['message']}")


def load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(p: Params) -> None:
    keep = p.model_dump(exclude={"force", "rescan", "exclude", "include", "exts", "only_existing", "skip_verify", "confirm"})
    old = load_config()
    for key in ("server", "token"):  # a Preview with these left empty must not wipe the saved ones
        if not keep[key]:
            keep[key] = old.get(key, "")
    CONFIG.write_text(json.dumps(keep), encoding="utf-8")
    os.chmod(CONFIG, 0o600)  # holds the pairing token


def remember(server: str, token: str) -> None:
    """Keep a server/token that worked (from the command line) so the screen starts with them filled in."""
    cfg = load_config()
    cfg.update(server=server, token=token)
    CONFIG.write_text(json.dumps(cfg), encoding="utf-8")
    os.chmod(CONFIG, 0o600)


def create_app(key: str | None = None, defaults: dict | None = None) -> FastAPI:
    key = key or secrets.token_urlsafe(16)
    job = Job()
    app = FastAPI(title="Mac agent")
    page = (Path(__file__).parent / "ui.html").read_text(encoding="utf-8")

    @app.middleware("http")
    async def guard(request: Request, call_next):
        """Only this Mac's browser may drive the agent: right Host (DNS rebinding) and the per-run key (other sites)."""
        if request.headers.get("host", "").split(":")[0] not in HOSTS:
            return JSONResponse({"detail": "bad host"}, status_code=403)
        given = request.headers.get("x-ui-key") or request.query_params.get("k")  # <img> can't send headers
        if request.url.path.startswith("/api/") and given != key:
            return JSONResponse({"detail": "bad key"}, status_code=403)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return page.replace("__UI_KEY__", key)

    @app.get("/api/config")
    def get_config():
        cfg = load_config()
        cfg.update({k: v for k, v in (defaults or {}).items() if v})  # given on the command line: wins over saved
        return cfg

    @app.get("/api/pair")
    def pair(link: str):
        """Split a pairing link into server + token."""
        try:
            server, token = parse_pairing(link)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"server": server, "token": token}

    @app.get("/api/discover")
    def discover(server: str):
        """Backend on this Mac (loopback) hands out its token without one; a remote one answers 401/403."""
        try:
            r = httpx.get(server.rstrip("/") + "/api/pairing", timeout=4)
            if r.status_code == 200:
                return {"token": r.json()["token"]}
        except (httpx.HTTPError, ValueError, KeyError):
            pass
        return {"token": None}

    @app.get("/api/server")
    def server_status(server: str = "", token: str = ""):
        ok, why = probe_server(server, token)
        return {"ok": ok, "reason": why}

    @app.get("/api/phone")
    async def phone():
        if job.running:  # don't poke the phone mid-transfer
            return {"connected": True, "busy": True}
        s = await device_state()
        return {**s, "hint": REASONS.get(s.get("reason", ""), "")}

    @app.post("/api/start")
    def start(p: Params):
        save_config(p)
        job.start(p)
        return {"ok": True}

    @app.post("/api/preview")
    def preview(p: Params):
        save_config(p)
        job.start(p, preview=True)
        return {"ok": True}

    @app.post("/api/delete")
    def delete(p: Params):
        """Delete the previewed, selected files from the phone, each only after the server is proven to hold it."""
        word = "DELETE UNVERIFIED" if p.skip_verify else "DELETE"
        if p.confirm != word:
            raise HTTPException(400, f"Type {word} to confirm")
        if not job.files:
            raise HTTPException(409, "Preview the files first, so you can see exactly what will be deleted")
        job.start(p, delete=True)
        return {"ok": True}

    @app.post("/api/replace_skipped")
    def replace_skipped(body: ReplaceRequest):
        """Re-send, replacing, the files the last copy skipped because the server already had them."""
        if job.last_params is None or not job.skipped_paths:
            raise HTTPException(409, "Nothing was skipped in the last copy")
        job.start(job.last_params.model_copy(update={
            "force": True, "include": list(job.skipped_paths), "exclude": [], "exts": None, "only_existing": False,
            "rescan": False, "dest": body.dest,
        }))
        return {"ok": True}

    @app.get("/api/files")
    def files(offset: int = 0, limit: int = 48, exts: str | None = None, hide_existing: bool = False):
        """A page of the last preview. `exts` (comma list, e.g. ".heic,.jpg") limits it; empty means no extension."""
        rows = job.files if exts is None else [f for f in job.files if ext_key(f) in set(exts.split(","))]
        if hide_existing:
            rows = [f for f in rows if f.path not in job.existing]
        page = rows[offset: offset + limit]
        return {"total": len(rows), "items": [
            {"path": f.path, "name": f.name, "kind": f.kind, "size": f.size, "mtime": f.mtime.isoformat(),
             "exists": f.path in job.existing}
            for f in page
        ]}

    @app.get("/api/thumb")
    async def thumb(path: str, size: int = 200):
        f = job.by_path.get(path)
        if f is None:
            raise HTTPException(404, "Unknown file")
        if f.ext == ".aae":  # edit data has no picture of its own: show the photo it belongs to
            folder, _, name = f.path.rpartition("/")
            f = job.stems.get((folder, name.rsplit(".", 1)[0].lower()))
            if f is None:
                raise HTTPException(404, "No photo for this edit file")
        try:
            make = videothumb.video_thumb if f.kind == "videos" else thumbs.device_thumb
            return Response(await make(f, min(size, 400)), media_type="image/jpeg",
                            headers={"Cache-Control": "private, max-age=3600"})
        except Exception:  # noqa: BLE001 - videos, huge or unreadable files: the page shows a placeholder
            raise HTTPException(404, "No preview")

    @app.post("/api/stop")
    def stop():
        job.stop()
        return {"ok": True}

    @app.get("/api/state")
    def state():
        return {**job.state, "running": job.running}

    app.state.job = job
    return app


def serve(port: int = 8770, open_browser: bool = True, defaults: dict | None = None) -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)  # thumbnail cache (see mac_agent/__init__.py)
    app = create_app(defaults=defaults)
    app.state.job._log("Screen ready. Preview the phone, then copy.")
    url = f"http://127.0.0.1:{port}/"
    print(f"Mac agent screen: {url}  (Ctrl-C to quit). Activity is logged below and on the page.", flush=True)
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
