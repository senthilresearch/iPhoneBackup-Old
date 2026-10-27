"""Chunked, resumable upload receiver for iPhone-side clients (web page or native app).

Flow:  POST /api/upload/init -> PUT /api/upload/{id}?offset=N (repeat) -> POST /api/upload/{id}/complete
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import importer
from .config import LIBRARY_ROOT
from .library import FolderError, classify, connect, init_library, safe_folder, safe_name

router = APIRouter(prefix="/api/upload")


def _tmp(upload_id: str) -> Path:
    if not upload_id.isalnum():
        raise HTTPException(404, "Unknown upload")
    return LIBRARY_ROOT / ".tmp" / f"up_{upload_id}"


def _meta(upload_id: str) -> dict:
    try:
        return json.loads(_tmp(upload_id).with_suffix(".json").read_text(encoding="utf-8"))
    except OSError:
        raise HTTPException(404, "Unknown upload")


class InitRequest(BaseModel):
    client_id: str  # stable id of the sending phone/browser
    local_id: str  # stable id of the item on that phone (PhotoKit localIdentifier, or name+size+mtime)
    name: str
    size: int
    taken_at: datetime | None = None
    destination: str | None = None
    replace: bool = False  # re-send even if already received, replacing the stored copy


class CheckItem(BaseModel):
    local_id: str
    name: str


class CheckRequest(BaseModel):
    client_id: str
    items: list[CheckItem]


@router.post("/check")
def check(req: CheckRequest):
    """Which of these items has this client already sent? Lets a sender skip them without one request per file."""
    init_library()
    device_id = f"app:{req.client_id}"
    by_kind: dict[str, list[str]] = {}
    for it in req.items:
        kind = classify(Path(safe_name(it.name)).suffix) or "files"
        by_kind.setdefault(kind, []).append(f"app:{it.local_id}")
    exists: list[str] = []
    info: dict[str, dict] = {}
    for kind, paths in by_kind.items():
        with connect(kind) as conn:
            for i in range(0, len(paths), 500):  # stay under SQLite's variable limit
                chunk = paths[i:i + 500]
                rows = conn.execute(
                    f"SELECT device_path, size, sha256 FROM items WHERE device_id=? "
                    f"AND device_path IN ({','.join('?' * len(chunk))})",
                    [device_id, *chunk],
                ).fetchall()
                for r in rows:
                    local = r["device_path"][len("app:"):]
                    exists.append(local)
                    info[local] = {"size": r["size"], "sha256": r["sha256"]}
    return {"exists": exists, "info": info}


@router.post("/init")
def init(req: InitRequest):
    init_library()
    try:
        folder = safe_folder(req.destination)
    except FolderError as e:
        raise HTTPException(400, str(e))
    name = safe_name(req.name)
    kind = classify(Path(name).suffix) or "files"
    device_id, device_path = f"app:{req.client_id}", f"app:{req.local_id}"
    with connect(kind) as conn:
        if not req.replace and conn.execute(
            "SELECT 1 FROM items WHERE device_id=? AND device_path=?", (device_id, device_path)
        ).fetchone():
            return {"status": "exists"}
    when = req.taken_at or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    upload_id = uuid.uuid4().hex
    base = _tmp(upload_id)
    base.with_suffix(".part").write_bytes(b"")
    base.with_suffix(".json").write_text(
        json.dumps({
            "kind": kind, "device_id": device_id, "device_path": device_path, "name": name,
            "size": req.size, "when": when.isoformat(), "folder": folder, "replace": req.replace,
        }),
        encoding="utf-8",
    )
    return {"status": "ready", "upload_id": upload_id, "received": 0, "kind": kind}


@router.get("/{upload_id}")
def status(upload_id: str):
    _meta(upload_id)
    return {"received": _tmp(upload_id).with_suffix(".part").stat().st_size}


@router.put("/{upload_id}")
async def put_chunk(upload_id: str, offset: int, request: Request):
    meta = _meta(upload_id)
    part = _tmp(upload_id).with_suffix(".part")
    have = part.stat().st_size
    if offset != have:
        raise HTTPException(409, f"Expected offset {have}")
    body = await request.body()
    if have + len(body) > meta["size"]:
        raise HTTPException(413, "More data than announced")
    with open(part, "ab") as f:
        f.write(body)
    return {"received": have + len(body)}


class CompleteRequest(BaseModel):
    sha256: str | None = None  # the sender's own checksum of the file; a mismatch means it was damaged in transit


@router.post("/{upload_id}/complete")
def complete(upload_id: str, req: CompleteRequest | None = None):
    meta = _meta(upload_id)
    part = _tmp(upload_id).with_suffix(".part")
    if part.stat().st_size != meta["size"]:
        raise HTTPException(409, f"Incomplete: {part.stat().st_size} of {meta['size']} bytes")
    sha = hashlib.sha256()
    with open(part, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            sha.update(block)
    if req and req.sha256 and req.sha256.lower() != sha.hexdigest():
        part.unlink(missing_ok=True)  # damaged: forget it so the sender starts this file again
        _tmp(upload_id).with_suffix(".json").unlink(missing_ok=True)
        raise HTTPException(409, "Checksum mismatch: the file was damaged in transit")
    status_ = importer.store_file(
        part, meta["kind"], meta["device_id"], meta["device_path"], meta["name"], meta["size"],
        sha.hexdigest(), datetime.fromisoformat(meta["when"]), meta["folder"],
        meta.get("replace", False),
    )
    _tmp(upload_id).with_suffix(".json").unlink(missing_ok=True)
    return {"status": status_}
