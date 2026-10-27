"""Sync logic: scan the phone, then init -> PUT chunks -> complete for every file the server lacks.

Importable without FastAPI. `client` is any httpx.Client already configured with base_url and the
X-Token header, which lets tests drive the real app through httpx's TestClient.
"""
import hashlib
import time
from urllib.parse import parse_qs, urlsplit
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx

from app.device import DeviceFile, read_file, scan

from .favorites import path_key

CHUNK = 4 << 20
RETRIES = 4


class AuthError(Exception):
    pass


class UploadError(Exception):
    pass


class Stopped(Exception):
    pass


@dataclass
class Summary:
    uploaded: int = 0
    duplicates: int = 0
    skipped: int = 0  # already on the server from this phone
    failed: list[tuple[str, str]] = field(default_factory=list)
    bytes_sent: int = 0
    planned: list[DeviceFile] = field(default_factory=list)  # dry-run
    stopped: bool = False
    skipped_paths: list[str] = field(default_factory=list)  # phone paths the server already had
    hashes: dict[str, str] = field(default_factory=dict)  # phone path -> sha256 of what was sent


def _check(r: httpx.Response) -> httpx.Response:
    if r.status_code == 401:
        raise AuthError("Server rejected the pairing token")
    return r


def check_existing(client, udid: str, files: list[DeviceFile]) -> set[str]:
    """Phone paths the server already holds from this phone (empty if it can't tell, e.g. an older backend)."""
    out: set[str] = set()
    try:
        for i in range(0, len(files), 500):
            batch = files[i:i + 500]
            r = client.post("/api/upload/check", json={
                "client_id": f"mac-{udid}", "items": [{"local_id": f.path, "name": f.name} for f in batch],
            })
            if r.status_code in (404, 405):  # backend without /check: fall back to asking per file
                return set()
            _check(r).raise_for_status()
            out.update(r.json()["exists"])
    except httpx.TransportError:
        return set()
    return out


def preflight(client: httpx.Client) -> None:
    """Fail fast on a wrong token or unreachable server."""
    _check(client.get("/api/device")).raise_for_status()


def parse_pairing(link: str) -> tuple[str, str]:
    """(server, token) from a pairing link such as http://pc:8081/upload.html?token=abc."""
    u = urlsplit(link.strip())
    token = (parse_qs(u.query).get("token") or [""])[0]
    if u.scheme not in ("http", "https") or not u.netloc or not token:
        raise ValueError("Not a pairing link (expected http://host:port/upload.html?token=...)")
    return f"{u.scheme}://{u.netloc}", token


def day_start(day: str) -> datetime:
    """YYYY-MM-DD as midnight in this Mac's local time (aware)."""
    return datetime.strptime(day, "%Y-%m-%d").astimezone()


def day_after(day: str) -> datetime:
    """Exclusive upper bound that makes `--until DAY` include the whole of DAY."""
    return day_start(day) + timedelta(days=1)


def ext_key(f: DeviceFile) -> str:
    return f.ext or "(none)"  # what the screen and --exts call a file without an extension


def normalize_exts(text: str) -> set[str]:
    """'heic, .JPG,none' -> {'.heic', '.jpg', '(none)'}."""
    out = set()
    for part in text.split(","):
        part = part.strip().lower()
        if not part:
            continue
        out.add("(none)" if part in ("none", "(none)") else part if part.startswith(".") else "." + part)
    return out


def extension_counts(files: list[DeviceFile]) -> list[dict]:
    """[{ext, count, bytes}] for the extensions present, most files first."""
    seen: dict[str, list[int]] = {}
    for f in files:
        row = seen.setdefault(ext_key(f), [0, 0])
        row[0] += 1
        row[1] += f.size
    return [{"ext": e, "count": c, "bytes": b} for e, (c, b) in sorted(seen.items(), key=lambda kv: (-kv[1][0], kv[0]))]


def select(files: list[DeviceFile], kinds: set[str] | None, since: datetime | None,
           until: datetime | None = None, exts: set[str] | None = None) -> list[DeviceFile]:
    """Filter by kind, extension and modified time: since <= mtime < until (until is exclusive; see day_after)."""
    return [
        f for f in files
        if (not kinds or f.kind in kinds)
        and (exts is None or ext_key(f) in exts)
        and (since is None or f.mtime >= since)
        and (until is None or f.mtime < until)
    ]


def _put(client, upload_id: str, pos: int, data: bytes, sleep) -> int:
    """Send one chunk at `pos`; returns the server's received byte count. Retries with backoff."""
    last = "unknown error"
    for attempt in range(RETRIES):
        try:
            r = _check(client.put(f"/api/upload/{upload_id}", params={"offset": pos}, content=data))
            if r.status_code == 409:  # offset mismatch: maybe an earlier try landed after we timed out
                got = client.get(f"/api/upload/{upload_id}").json()["received"]
                if got == pos + len(data):
                    return got
                raise UploadError(f"offset mismatch (server has {got}, expected {pos})")
            if r.status_code < 500:
                r.raise_for_status()
                return r.json()["received"]
            last = f"HTTP {r.status_code}"
        except httpx.TransportError as e:
            last = type(e).__name__
        sleep(2 ** attempt)
        try:  # realign after a network drop
            got = client.get(f"/api/upload/{upload_id}").json()["received"]
            if got == pos + len(data):
                return got
        except httpx.TransportError:
            pass
    raise UploadError(f"gave up after {RETRIES} tries ({last})")


async def upload_one(client, afc, udid: str, f: DeviceFile, destination: str | None,
                     on_bytes=None, replace=False, sleep=time.sleep, should_stop=lambda: False, hashes=None) -> str:
    """Returns 'copied' | 'duplicate' | 'exists'."""
    r = _check(client.post("/api/upload/init", json={
        "client_id": f"mac-{udid}", "local_id": f.path, "name": f.name, "size": f.size,
        "taken_at": f.mtime.isoformat(), "destination": destination,
        "replace": replace,
    }))
    r.raise_for_status()
    info = r.json()
    if info["status"] == "exists":
        return "exists"
    upload_id, received = info["upload_id"], info["received"]
    pos = 0
    digest = hashlib.sha256()
    async for data in read_file(afc, f.path, f.size, CHUNK):
        if should_stop():
            raise Stopped()
        digest.update(data)  # every byte read from the phone, including parts the server already has
        end = pos + len(data)
        if end <= received:  # resumed upload: server already has this part
            pos = end
            continue
        if pos < received:
            data, pos = data[received - pos:], received
        received = _put(client, upload_id, pos, data, sleep)
        if on_bytes:
            on_bytes(len(data))
        pos = received
    r = client.post(f"/api/upload/{upload_id}/complete", json={"sha256": digest.hexdigest()})
    if r.status_code != 200:
        raise UploadError(f"complete failed: HTTP {r.status_code} {r.text[:200]}")
    if hashes is not None:
        hashes[f.path] = digest.hexdigest()
    return r.json()["status"]


async def sync(client, afc, udid: str, *, kinds=None, since=None, until=None, exts=None, destination=None, dry_run=False, force=False,
               files=None, known=None, log=print, sleep=time.sleep, should_stop=lambda: False, on_progress=None) -> Summary:
    """`on_progress(dict)` is called as bytes flow; `should_stop()` is polled between chunks."""
    summary = Summary()
    if files is None:  # callers that already scanned (the browser screen) pass the selection in
        log("Scanning the phone (about a minute for a large library, please wait)...")
        files = select(await scan(afc, None, kinds, known), kinds, since, until, exts)
    total = sum(f.size for f in files)
    log(f"{len(files)} files ({total / 1e6:.1f} MB) match.")
    if dry_run:
        summary.planned = files
        return summary
    started = time.monotonic()
    existing = set() if force else check_existing(client, udid, files)  # one request per 500 files, not per file
    if existing:
        log(f"{len(existing)} of these are already on the server and will be skipped.")

    cur = {"index": 0, "total": len(files), "name": "", "file_size": 0, "file_bytes": 0,
           "total_bytes": total, "bytes_sent": 0, "speed": 0.0}

    def report():
        cur.update(bytes_sent=summary.bytes_sent, uploaded=summary.uploaded, duplicates=summary.duplicates,
                   skipped=summary.skipped, failed=len(summary.failed))
        cur["speed"] = summary.bytes_sent / max(time.monotonic() - started, 1e-6)
        if on_progress:
            on_progress(dict(cur))

    def on_bytes(n):
        summary.bytes_sent += n
        cur["file_bytes"] += n
        report()

    for i, f in enumerate(files, 1):
        if should_stop():
            summary.stopped = True
            break
        cur.update(index=i, name=f.path, file_size=f.size, file_bytes=0)
        if f.path in existing:
            summary.skipped += 1
            summary.skipped_paths.append(f.path)
            report()
            continue
        report()
        try:
            status = await upload_one(client, afc, udid, f, destination, on_bytes, replace=force, sleep=sleep,
                                      should_stop=should_stop, hashes=summary.hashes)
        except Stopped:
            summary.stopped = True
            log("Stopped.")
            break
        except AuthError:
            raise
        except (UploadError, httpx.HTTPError, OSError) as e:
            summary.failed.append((f.path, str(e)))
            log(f"[{i}/{len(files)}] FAILED {f.path}: {e}")
            continue
        if status == "copied":
            summary.uploaded += 1
        elif status == "duplicate":
            summary.duplicates += 1
        else:
            summary.skipped += 1
            summary.skipped_paths.append(f.path)
        report()
        speed = summary.bytes_sent / max(time.monotonic() - started, 1e-6)
        log(f"[{i}/{len(files)}] {status:9} {f.path}  ({speed / 1e6:.1f} MB/s)")
    return summary



# ---- delete from the phone ---------------------------------------------------------------------------------

@dataclass
class DeleteSummary:
    deleted: int = 0
    bytes_freed: int = 0
    kept: list[tuple[str, str]] = field(default_factory=list)  # (phone path, why it was not deleted)
    failed: list[tuple[str, str]] = field(default_factory=list)  # the phone refused or errored
    deleted_paths: list[str] = field(default_factory=list)
    stopped: bool = False


def check_info(client, udid: str, files: list[DeviceFile]) -> dict[str, dict]:
    """phone path -> {size, sha256} for what the server holds from this phone. Raises if it can't be asked."""
    out: dict[str, dict] = {}
    for i in range(0, len(files), 500):
        batch = files[i:i + 500]
        r = client.post("/api/upload/check", json={
            "client_id": f"mac-{udid}", "items": [{"local_id": f.path, "name": f.name} for f in batch],
        })
        _check(r).raise_for_status()
        out.update(r.json().get("info") or {})
    return out


async def sha256_of(afc, f: DeviceFile) -> str:
    digest = hashlib.sha256()
    async for chunk in read_file(afc, f.path, f.size, CHUNK):
        digest.update(chunk)
    return digest.hexdigest()


async def delete_from_phone(client, afc, udid: str, files: list[DeviceFile], *, favorites, hashes=None, log=print,
                            should_stop=lambda: False, on_progress=None, verify=True) -> DeleteSummary:
    """Delete each file from the phone, but only once the server is proven to hold an identical copy.

    Right before deleting a file: the server must have it (from this phone), with the same size and the same
    SHA-256 as the phone's file. The checksum comes from `hashes` (computed while this session sent it) or, failing
    that, from reading the file off the phone again. Anything else is kept, with the reason.
    `favorites` (required, from favorites.fetch_favorites) is the set of Favourite keys: those files, their Live Photo
    videos, edit data and edited versions are always kept.
    `verify=False` (the user's explicit choice when the server is unreachable) skips the server check: files are deleted
    WITHOUT proof that a copy exists anywhere. Favourites are still kept.
    """
    s = DeleteSummary()
    hashes = hashes or {}
    info = check_info(client, udid, files) if verify else {}
    if not verify:
        log("WARNING: deleting WITHOUT checking the server. Files are not verified to be backed up (Favourites are still kept).")
    cur = {"index": 0, "total": len(files), "name": "", "deleted": 0, "kept": 0, "failed": 0, "bytes_freed": 0}

    def report():
        cur.update(deleted=s.deleted, kept=len(s.kept), failed=len(s.failed), bytes_freed=s.bytes_freed)
        if on_progress:
            on_progress(dict(cur))

    for i, f in enumerate(files, 1):
        if should_stop():
            s.stopped = True
            log("Stopped.")
            break
        cur.update(index=i, name=f.path)
        report()
        row = info.get(f.path)
        if path_key(f.path) in favorites:
            why = "a Favourite"
        elif not verify:
            why = ""
        elif row is None:
            why = "not on the server"
        elif row["size"] != f.size:
            why = f"size differs (phone {f.size}, server {row['size']})"
        else:
            try:
                mine = hashes.get(f.path) or await sha256_of(afc, f)
            except (OSError, ValueError) as e:
                mine, why = None, f"could not read it to verify ({e})"
            else:
                why = "" if mine == row["sha256"] else "checksum differs from the server's copy"
        if why:
            s.kept.append((f.path, why))
            log(f"[{i}/{len(files)}] KEPT    {f.path}: {why}")
            report()
            continue
        try:
            await afc.rm_single(f.path)
        except Exception as e:  # noqa: BLE001 - the phone refused (locked, protected, gone)
            s.failed.append((f.path, f"{type(e).__name__}: {e}"))
            log(f"[{i}/{len(files)}] FAILED  {f.path}: {e}")
        else:
            s.deleted += 1
            s.bytes_freed += f.size
            s.deleted_paths.append(f.path)
            log(f"[{i}/{len(files)}] deleted {f.path}")
        report()
    return s
