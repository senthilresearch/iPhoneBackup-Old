"""Which phone files are Favourites, read from the phone's own Photos database (/PhotoData/Photos.sqlite).

Delete-from-phone must never remove these. If the database can't be read, FavoritesUnavailable is raised and the
caller must not delete anything.
"""
import asyncio
import re
import sqlite3
import tempfile
from pathlib import Path, PurePosixPath

DB = "/PhotoData/Photos.sqlite"
Key = tuple[str, str]  # (folder like "DCIM/106APPLE", base name without extension and without the edit "E")


class FavoritesUnavailable(Exception):
    pass


def key_for(directory: str, filename: str) -> Key:
    """Companions share a key: IMG_1234.HEIC, its Live Photo IMG_1234.MOV, IMG_1234.AAE, and the edit IMG_E1234.JPG."""
    stem = filename.rsplit(".", 1)[0]
    return directory.strip("/"), re.sub(r"^([A-Za-z]+_)E(\d+)$", r"\1\2", stem)


def path_key(path: str) -> Key:
    p = PurePosixPath(path)
    return key_for(str(p.parent), p.name)


def read_favorites(db_file: Path) -> set[Key]:
    try:
        con = sqlite3.connect(str(db_file))
        try:
            total = con.execute("SELECT COUNT(*) FROM ZASSET").fetchone()[0]
            rows = con.execute("SELECT ZDIRECTORY, ZFILENAME FROM ZASSET WHERE ZFAVORITE = 1").fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        raise FavoritesUnavailable(f"the Photos database has an unexpected layout or is damaged ({e})") from e
    if total == 0:
        raise FavoritesUnavailable("the Photos database lists no photos, so favourites can't be trusted")
    return {key_for(d, n) for d, n in rows if d and n}


async def fetch_favorites(afc) -> set[Key]:
    """Copy the Photos database (with its pending changes) off the phone and read the favourites from the copy."""
    with tempfile.TemporaryDirectory(prefix="iphone-favs-") as tmp:
        try:
            for suffix in ("", "-wal", "-shm"):
                try:
                    data = await afc.get_file_contents(DB + suffix)
                except FileNotFoundError:
                    if suffix == "":
                        raise
                    continue
                (Path(tmp) / f"Photos.sqlite{suffix}").write_bytes(data)
        except Exception as e:  # noqa: BLE001 - locked phone, missing file, dropped connection
            raise FavoritesUnavailable(f"could not read the Photos database from the phone ({type(e).__name__}: {e})") from e
        return await asyncio.to_thread(read_favorites, Path(tmp) / "Photos.sqlite")
