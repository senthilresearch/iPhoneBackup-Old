import asyncio
import hmac
import socket
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel

from . import importer, settings, thumbs, uploads, watcher
from .catalog import Filter, Selection, catalog
from .config import DATA_ROOT, LIBRARY_ROOT, PORT, PUBLIC_URL, TOKEN_IS_ADMIN
from .library import (
    KINDS,
    FolderError,
    connect,
    create_folder,
    delete_folder,
    init_library,
    list_folders,
    safe_folder,
)

LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}

init_library()


@asynccontextmanager
async def lifespan(_app):
    task = asyncio.create_task(watcher.watch())
    yield
    task.cancel()
    await thumbs.reset()


app = FastAPI(title="iPhoneBackup", lifespan=lifespan)
app.include_router(uploads.router)


@app.middleware("http")
async def require_token_off_machine(request: Request, call_next):
    """The PC itself is trusted; other devices on the LAN must present the pairing token."""
    host = request.client.host if request.client else ""
    if host not in LOOPBACK and request.url.path.startswith("/api/"):
        given = request.headers.get("x-token") or request.query_params.get("token") or ""
        if not hmac.compare_digest(given, settings.load()["token"]):
            return JSONResponse({"detail": "Pairing token required"}, status_code=401)
        request.state.authed = True
    return await call_next(request)


def local_only(request: Request):
    if TOKEN_IS_ADMIN and getattr(request.state, "authed", False):
        return
    if (request.client.host if request.client else "") not in LOOPBACK:
        raise HTTPException(403, "Only available on this PC")


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


# ---- device & live state -------------------------------------------------------------------

@app.get("/api/device")
def get_device():
    return {**watcher.state, "auto_import": settings.load()["auto_import"]}


@app.get("/api/events")
async def events():
    return StreamingResponse(
        watcher.event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


class SettingsUpdate(BaseModel):
    auto_import: bool


@app.put("/api/settings")
def put_settings(body: SettingsUpdate, request: Request):
    local_only(request)
    return {"auto_import": settings.update(auto_import=body.auto_import)["auto_import"]}


@app.get("/api/pairing")
def pairing(request: Request):
    local_only(request)
    token = settings.load()["token"]
    host = request.headers.get("host", "")
    if PUBLIC_URL:
        base = PUBLIC_URL.rstrip("/")
    elif host and host.split(":")[0] not in LOOPBACK:
        base = f"http://{host}"  # the address the browser used to reach us (e.g. behind the Docker web container)
    else:
        base = f"http://{lan_ip()}:{PORT}"
    return {"token": token, "upload_url": f"{base}/upload.html?token={token}", "base_url": base}


# ---- what is on the phone ----------------------------------------------------------------

@app.post("/api/device/rescan")
async def rescan():
    if not watcher.state.get("connected"):
        raise HTTPException(409, "No iPhone connected")
    if catalog.scanning:
        return {"started": False}
    asyncio.create_task(catalog.refresh())
    return {"started": True}


def _flt(kind: list[str], date_from: date | None, date_to: date | None, hide_imported: bool) -> Filter:
    kinds = [k for k in kind if k in KINDS] or ["photos", "videos"]
    return Filter(kinds=kinds, date_from=date_from, date_to=date_to, hide_imported=hide_imported)


@app.get("/api/device/files")
def device_files(
    kind: list[str] = Query(["photos", "videos"]),
    date_from: date | None = None,
    date_to: date | None = None,
    hide_imported: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(60, ge=1, le=500),
):
    flt = _flt(kind, date_from, date_to, hide_imported)
    matches = catalog.select(flt)
    known = catalog.imported_paths() if catalog.device_id else {k: set() for k in KINDS}
    page = matches[offset : offset + limit]
    return {
        "total": len(matches),
        "total_bytes": sum(f.size for f in matches),
        "catalog": catalog.meta(),
        "items": [
            {
                "path": f.path, "name": f.name, "kind": f.kind, "size": f.size,
                "taken_at": f.mtime.astimezone().isoformat(timespec="seconds"),
                "imported": f.path in known[f.kind],
            }
            for f in page
        ],
    }


@app.get("/api/device/thumb")
async def device_thumb(path: str, size: int = Query(240, ge=64, le=1600)):
    f = next((x for x in catalog.files if x.path == path), None)
    if not f:
        raise HTTPException(404, "Not on the phone")
    if not watcher.state.get("connected"):
        raise HTTPException(409, "No iPhone connected")
    try:
        data = await thumbs.device_thumb(f, size)
    except ValueError:
        raise HTTPException(415, "No preview")
    except Exception:
        raise HTTPException(502, "Could not read from the phone")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


# ---- importing ---------------------------------------------------------------------------

class ImportRequest(Selection):
    destination: str | None = None


@app.post("/api/import")
async def start_import(req: ImportRequest = ImportRequest()):
    if not watcher.state.get("connected"):
        raise HTTPException(409, "No iPhone connected")
    try:
        importer.start(req, req.destination)
    except FolderError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"started": True}


@app.post("/api/import/pause")
def pause_import():
    importer.pause()
    return asdict(importer.progress)


@app.post("/api/import/resume")
def resume_import():
    importer.resume()
    return asdict(importer.progress)


@app.post("/api/import/cancel")
def cancel_import():
    importer.cancel()
    return {"cancelling": True}


@app.get("/api/import/status")
def import_status():
    return asdict(importer.progress)


# ---- folders -----------------------------------------------------------------------------

class FolderBody(BaseModel):
    path: str


@app.get("/api/folders")
def get_folders(include_auto: bool = False):
    return {"folders": list_folders(include_auto)}


@app.post("/api/folders")
def post_folder(body: FolderBody):
    try:
        return {"folder": create_folder(body.path)}
    except FolderError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/folders")
def remove_folder(path: str):
    try:
        delete_folder(path)
    except FolderError as e:
        raise HTTPException(400, str(e))
    return {"deleted": True}


# ---- the library on this PC --------------------------------------------------------------

@app.get("/api/library")
def list_library(
    kind: list[str] = Query(["photos", "videos", "files"]),
    folder: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(60, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    kinds = [k for k in kind if k in KINDS]
    if folder:
        try:
            folder = safe_folder(folder)
        except FolderError as e:
            raise HTTPException(400, str(e))
    where, args = ["is_sidecar=0"], []
    if folder:
        where.append("(folder=? OR folder LIKE ?)")
        args += [folder, folder + "/%"]
    if date_from:
        where.append("date(taken_at, 'localtime') >= ?")
        args.append(date_from.isoformat())
    if date_to:
        where.append("date(taken_at, 'localtime') <= ?")
        args.append(date_to.isoformat())
    clause = " AND ".join(where)
    total, rows = 0, []
    for k in kinds:
        with connect(k) as conn:
            total += conn.execute(f"SELECT COUNT(*) FROM items WHERE {clause}", args).fetchone()[0]
            got = conn.execute(
                f"SELECT * FROM items WHERE {clause} ORDER BY taken_at DESC LIMIT ?", [*args, offset + limit]
            ).fetchall()
            rows += [{**dict(r), "kind": k} for r in got]
    rows.sort(key=lambda r: r["taken_at"], reverse=True)
    return {"total": total, "items": rows[offset : offset + limit]}


def _item_file(kind: str, item_id: int) -> Path:
    if kind not in KINDS:
        raise HTTPException(404, "Unknown kind")
    with connect(kind) as conn:
        row = conn.execute("SELECT stored_path FROM items WHERE id=?", (item_id,)).fetchone()
    path = LIBRARY_ROOT / row["stored_path"] if row else None
    if not path or not path.is_file():
        raise HTTPException(404, "Not found")
    return path


@app.get("/api/media/{kind}/{item_id}")
def get_media(kind: str, item_id: int):
    return FileResponse(_item_file(kind, item_id))


@app.get("/api/thumb/photos/{item_id}")
def get_thumb(item_id: int, size: int = Query(360, ge=64, le=2400)):
    cache = DATA_ROOT / ".thumbs" / f"{item_id}_{size}.jpg"
    if not cache.is_file():
        src = _item_file("photos", item_id)
        try:
            with Image.open(src) as img:
                img = ImageOps.exif_transpose(img)
                img.thumbnail((size, size))
                cache.parent.mkdir(exist_ok=True)
                img.convert("RGB").save(cache, "JPEG", quality=80)
        except Exception:
            raise HTTPException(415, "Cannot render thumbnail")
    return FileResponse(cache, headers={"Cache-Control": "max-age=86400"})


# Serve the built frontend (npm run build) when present; must come after the API routes.
_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
