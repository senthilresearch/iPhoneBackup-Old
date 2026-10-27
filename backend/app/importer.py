"""Copy items from the iPhone into the library: deduplicated, resumable, cancellable."""
import asyncio
import hashlib
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import device
from .catalog import Selection, catalog
from .config import LIBRARY_ROOT
from .device import DeviceFile
from .library import auto_folder, connect, init_library, kind_dir, safe_folder, safe_name


class Cancelled(Exception):
    pass


@dataclass
class Progress:
    state: str = "idle"  # idle | scanning | importing | paused | done | cancelled | error
    total: int = 0
    done: int = 0
    copied: int = 0
    skipped: int = 0
    duplicates: int = 0
    failed: int = 0
    bytes_total: int = 0
    bytes_done: int = 0
    current: str = ""
    current_size: int = 0
    current_bytes: int = 0
    speed: float = 0.0  # bytes/s
    eta: float | None = None  # seconds
    destination: str = ""
    started_at: float = 0.0
    error: str = ""
    errors: list[str] = field(default_factory=list)
    failed_paths: list[str] = field(default_factory=list)
    recent: list[str] = field(default_factory=list)


progress = Progress()
_resume = asyncio.Event()
_resume.set()
_cancel = False
_task: asyncio.Task | None = None


def is_running() -> bool:
    return progress.state in ("scanning", "importing", "paused")


def pause():
    if progress.state == "importing":
        _resume.clear()
        progress.state = "paused"


def resume():
    if progress.state == "paused":
        progress.state = "importing"
        _resume.set()


def cancel():
    global _cancel
    if is_running():
        _cancel = True
        _resume.set()  # wake a paused worker so it can exit


def start(selection: Selection, destination: str | None) -> None:
    """Begin an import in the background. Raises RuntimeError if one is running."""
    global _task, progress, _cancel
    if is_running():
        raise RuntimeError("Import already running")
    folder = safe_folder(destination)  # validate before starting
    _cancel = False
    _resume.set()
    progress = Progress(state="scanning", destination=folder)
    _task = asyncio.create_task(_run(selection, folder))


def _unique_dest(folder: Path, name: str) -> Path:
    dest = folder / name
    n = 1
    while dest.exists():
        dest = folder / f"{Path(name).stem}_{n}{Path(name).suffix}"
        n += 1
    return dest


def _forget(conn, device_id: str, device_path: str) -> None:
    old = conn.execute(
        "SELECT stored_path FROM items WHERE device_id=? AND device_path=?", (device_id, device_path)
    ).fetchone()
    if not old:
        return
    conn.execute("DELETE FROM items WHERE device_id=? AND device_path=?", (device_id, device_path))
    if not conn.execute("SELECT 1 FROM items WHERE stored_path=?", (old["stored_path"],)).fetchone():
        (LIBRARY_ROOT / old["stored_path"]).unlink(missing_ok=True)


def store_file(
    tmp: Path, kind: str, device_id: str, device_path: str, name: str, size: int,
    digest: str, when: datetime, folder: str, replace: bool = False,
) -> str:
    """Move a fully-received temp file into the library and record it. Returns 'copied' or 'duplicate'.

    `replace` first forgets any earlier copy of this source path (record and, if nothing else shares it, the file).
    """
    name = safe_name(name)
    ext = Path(name).suffix.lower()
    is_sidecar = int(ext == ".aae")
    with connect(kind) as conn:
        if replace:
            _forget(conn, device_id, device_path)
        same = None
        if not is_sidecar:
            same = conn.execute(
                "SELECT stored_path, folder FROM items WHERE sha256=? AND is_sidecar=0 LIMIT 1", (digest,)
            ).fetchone()
        if same:  # identical content already stored: drop the copy, remember this source path
            tmp.unlink(missing_ok=True)
            stored, rel_folder, status = same["stored_path"], same["folder"], "duplicate"
        else:
            rel_folder = folder or auto_folder(when)
            target = kind_dir(kind, rel_folder)
            target.mkdir(parents=True, exist_ok=True)
            dest = _unique_dest(target, name)
            os.replace(tmp, dest)
            stamp = when.timestamp()
            os.utime(dest, (stamp, stamp))
            stored, status = str(dest.relative_to(LIBRARY_ROOT)), "copied"
        conn.execute(
            "INSERT OR IGNORE INTO items (device_id, device_path, stored_path, folder, name, ext, size,"
            " sha256, taken_at, is_sidecar) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (device_id, device_path, stored, rel_folder, name, ext, size, digest, when.isoformat(), is_sidecar),
        )
    return status


async def _copy_one(afc, device_id: str, f: DeviceFile, folder: str) -> str:
    tmp = LIBRARY_ROOT / ".tmp" / f"{uuid.uuid4().hex}.part"
    sha = hashlib.sha256()
    progress.current, progress.current_size, progress.current_bytes = f.path, f.size, 0
    t0 = time.monotonic()
    try:
        with open(tmp, "wb") as out:
            async for chunk in device.read_file(afc, f.path, f.size):
                await _resume.wait()
                if _cancel:
                    raise Cancelled()
                out.write(chunk)
                sha.update(chunk)
                progress.current_bytes += len(chunk)
                progress.bytes_done += len(chunk)
                _update_rates(t0)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return store_file(tmp, f.kind, device_id, f.path, f.name, f.size, sha.hexdigest(), f.mtime, folder)


def _update_rates(_t0: float) -> None:
    elapsed = time.monotonic() - progress.started_at
    if elapsed > 1:
        progress.speed = progress.bytes_done / elapsed
        left = progress.bytes_total - progress.bytes_done
        progress.eta = left / progress.speed if progress.speed else None


async def _run(selection: Selection, folder: str) -> None:
    try:
        init_library()
        async with device.open_afc() as (device_id, afc):
            await catalog.ensure(device_id)
            files = catalog.resolve(selection)
            known = catalog.imported_paths()
            todo = [f for f in files if f.path not in known[f.kind]]
            progress.skipped = len(files) - len(todo)
            progress.total = len(todo)
            progress.bytes_total = sum(f.size for f in todo)
            progress.state = "importing"
            progress.started_at = time.monotonic()
            for f in todo:
                if _cancel:
                    raise Cancelled()
                try:
                    status = await _copy_one(afc, device_id, f, folder)
                    if status == "copied":
                        progress.copied += 1
                    else:
                        progress.duplicates += 1
                    progress.recent = [f.name, *progress.recent][:8]
                except Cancelled:
                    raise
                except Exception as e:  # noqa: BLE001 - keep going; failed files are retryable
                    progress.failed += 1
                    progress.errors.append(f"{f.path}: {e}")
                    progress.failed_paths.append(f.path)
                progress.done += 1
        progress.state, progress.current, progress.eta = "done", "", 0
    except Cancelled:
        progress.state, progress.current = "cancelled", ""
    except Exception as e:  # noqa: BLE001
        progress.state, progress.error = "error", f"{type(e).__name__}: {e}"
