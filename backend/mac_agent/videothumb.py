"""Video thumbnails on a Mac: copy the file from the phone, let QuickLook render a frame, cache a JPEG.

Needs the whole file (iPhone MOVs keep their index at the end), so big videos are skipped.
"""
import asyncio
import hashlib
import io
import sys
import tempfile
from pathlib import Path

from PIL import Image

from app import thumbs
from app.config import DATA_ROOT
from app.device import DeviceFile

MAX_VIDEO_BYTES = 150 * 1024 * 1024


async def _quicklook(src: Path, size: int) -> bytes | None:
    """PNG bytes of the file's QuickLook thumbnail, or None."""
    proc = await asyncio.create_subprocess_exec(
        "qlmanage", "-t", "-s", str(size), "-o", str(src.parent), str(src),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(proc.wait(), 30)
    except asyncio.TimeoutError:
        proc.kill()
        return None
    out = src.parent / f"{src.name}.png"
    return out.read_bytes() if out.exists() else None


def _to_jpeg(png: bytes) -> bytes:
    with Image.open(io.BytesIO(png)) as img:
        out = io.BytesIO()
        img.convert("RGB").save(out, "JPEG", quality=80)
        return out.getvalue()


async def video_thumb(f: DeviceFile, size: int) -> bytes:
    if sys.platform != "darwin":
        raise ValueError("video previews need macOS QuickLook")
    key = hashlib.sha1(f"v|{f.path}|{int(f.mtime.timestamp())}|{size}".encode()).hexdigest()
    cache = DATA_ROOT / ".devthumbs" / f"{key}.jpg"
    if cache.is_file():
        return cache.read_bytes()
    if f.size > MAX_VIDEO_BYTES:
        raise ValueError("video too large for a preview")
    data = await thumbs.fetch(f)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"video{f.ext}"
        src.write_bytes(data)
        png = await _quicklook(src, size)
    if not png:
        raise ValueError("no frame")
    jpg = await asyncio.to_thread(_to_jpeg, png)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(jpg)
    return jpg
