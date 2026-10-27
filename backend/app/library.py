"""Local library: folder layout, destination folders and one SQLite catalog per media type."""
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from .config import DATA_ROOT, LIBRARY_ROOT, PHOTO_EXT, SIDECAR_EXT, VIDEO_EXT

KINDS = ("photos", "videos", "files")
FOLDERS = {"photos": "Photos", "videos": "Videos", "files": "Files"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    device_id TEXT NOT NULL,
    device_path TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    folder TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    ext TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    taken_at TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_sidecar INTEGER NOT NULL DEFAULT 0,
    UNIQUE (device_id, device_path)
);
CREATE INDEX IF NOT EXISTS idx_items_sha ON items (sha256);
CREATE INDEX IF NOT EXISTS idx_items_taken ON items (taken_at);
"""

_BAD_CHARS = re.compile(r'[<>:"|?*\\/\x00-\x1f]')   # not valid in a file name on Windows, exFAT or NTFS (the library drive must open on any computer)
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_AUTO_FOLDER = re.compile(r"^\d{4}(/\d{2})?$")
MAX_DEPTH = 5


class FolderError(ValueError):
    pass


def classify(ext: str) -> str | None:
    ext = ext.lower()
    if ext in PHOTO_EXT or ext in SIDECAR_EXT:
        return "photos"
    if ext in VIDEO_EXT:
        return "videos"
    return None


def safe_name(name: str) -> str:
    """Make a device-supplied file name safe for Windows."""
    name = _BAD_CHARS.sub("_", name).rstrip(" .") or "file"
    if len(name) > 200:  # exFAT/NTFS allow 255 characters; keep the extension and leave room for " (2)" style suffixes
        stem, dot, ext = name.rpartition(".")
        name = (stem[:200 - len(ext) - 1] + dot + ext) if dot and len(ext) < 20 else name[:200]
    if name.split(".")[0].lower() in _RESERVED:
        name = "_" + name
    return name


def safe_folder(rel: str | None) -> str:
    """Validate a user-supplied sub-folder ('Trips/Goa 2026'); returns it normalized ('' = none)."""
    if not rel or not rel.strip():
        return ""
    parts = [p.strip() for p in re.split(r"[\\/]+", rel.strip()) if p.strip()]
    if len(parts) > MAX_DEPTH:
        raise FolderError(f"Folders can be at most {MAX_DEPTH} levels deep")
    for p in parts:
        if p in (".", "..") or _BAD_CHARS.search(p) or p.endswith((".", " ")) or len(p) > 60:
            raise FolderError(f"Invalid folder name: {p!r}")
        if p.lower() in _RESERVED:
            raise FolderError(f"Reserved folder name: {p!r}")
        if p.startswith("."):
            raise FolderError("Folder names cannot start with a dot")
    return "/".join(parts)


MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")  # fixed English names: strftime("%B") would follow the host locale


def auto_folder(when: datetime) -> str:
    """Default destination: <year>/<full month name>, e.g. 2026/October."""
    local = when.astimezone()
    return f"{local.year}/{MONTHS[local.month - 1]}"


def kind_dir(kind: str, folder: str = "") -> Path:
    base = LIBRARY_ROOT / FOLDERS[kind]
    target = (base / folder).resolve()
    if base.resolve() not in target.parents and target != base.resolve():
        raise FolderError("Folder escapes the library")
    return target


def list_folders(include_auto: bool = False) -> list[str]:
    found: set[str] = set()
    for kind in KINDS:
        base = LIBRARY_ROOT / FOLDERS[kind]
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.is_dir():
                found.add(p.relative_to(base).as_posix())
    if not include_auto:
        found = {f for f in found if not _AUTO_FOLDER.match(f)}
    return sorted(found, key=str.lower)


def create_folder(rel: str) -> str:
    folder = safe_folder(rel)
    if not folder:
        raise FolderError("Folder name is empty")
    for kind in KINDS:
        kind_dir(kind, folder).mkdir(parents=True, exist_ok=True)
    return folder


def delete_folder(rel: str) -> None:
    """Remove a folder that is empty in all three media areas."""
    folder = safe_folder(rel)
    if not folder:
        raise FolderError("Folder name is empty")
    dirs = [kind_dir(k, folder) for k in KINDS]
    for d in dirs:
        if d.is_dir() and any(d.iterdir()):
            raise FolderError("Folder is not empty")
    for d in dirs:
        if d.is_dir():
            shutil.rmtree(d)


def db_path(kind: str) -> Path:
    return DATA_ROOT / "db" / f"{kind}.db"


def connect(kind: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(kind))
    conn.row_factory = sqlite3.Row
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
    if "folder" not in cols:
        conn.execute("ALTER TABLE items ADD COLUMN folder TEXT NOT NULL DEFAULT ''")
    rows = conn.execute("SELECT id, stored_path FROM items WHERE folder=''").fetchall()
    for r in rows:
        parts = Path(r["stored_path"].replace("\\", "/")).parent.parts[1:]
        conn.execute("UPDATE items SET folder=? WHERE id=?", ("/".join(parts), r["id"]))
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_folder ON items (folder)")


def init_library() -> None:
    """Create folders and databases on first run; safe to call repeatedly."""
    (DATA_ROOT / "db").mkdir(parents=True, exist_ok=True)
    (LIBRARY_ROOT / ".tmp").mkdir(parents=True, exist_ok=True)
    for kind in KINDS:
        (LIBRARY_ROOT / FOLDERS[kind]).mkdir(exist_ok=True)
        with connect(kind) as conn:
            conn.executescript(SCHEMA)
            _migrate(conn)
