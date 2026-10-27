"""Remembers what the phone said about each file, so a later scan only asks about new files.

Entries are path -> ["d"] (folder) or ["f", size, mtime]. A file changed in place keeps its old facts until a full
rescan (`--rescan`, or the screen's Rescan button); photos and videos in /DCIM are not rewritten, so this is rare.
"""
import json
import re

from app.config import DATA_ROOT


def _path(udid: str):
    return DATA_ROOT / ".cache" / f"mac_scan_{re.sub(r'[^A-Za-z0-9-]', '', udid)}.json"


def load(udid: str) -> dict:
    try:
        return json.loads(_path(udid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(udid: str, known: dict) -> None:
    path = _path(udid)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(known), encoding="utf-8")
    tmp.replace(path)
