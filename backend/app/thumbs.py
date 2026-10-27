"""Thumbnails for items still on the phone (not imported yet), cached on disk."""
import asyncio
import hashlib
import io

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from . import device
from .config import DATA_ROOT

register_heif_opener()

MAX_BYTES = 40 * 1024 * 1024
_lock = asyncio.Lock()
_cm = None
_afc = None


async def reset() -> None:
    """Drop the shared phone connection (call when the phone disconnects)."""
    global _cm, _afc
    cm, _cm, _afc = _cm, None, None
    if cm:
        try:
            await cm.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass


async def _connection():
    global _cm, _afc
    if _afc is None:
        _cm = device.open_afc()
        _, _afc = await _cm.__aenter__()
    return _afc


def _render(data: bytes, size: int) -> bytes:
    with Image.open(io.BytesIO(data)) as img:
        img = ImageOps.exif_transpose(img)
        img.thumbnail((size, size))
        out = io.BytesIO()
        img.convert("RGB").save(out, "JPEG", quality=80)
        return out.getvalue()


async def fetch(f: device.DeviceFile) -> bytes:
    """Read a whole phone file over the shared connection (one reader at a time)."""
    async with _lock:
        try:
            afc = await _connection()
            return b"".join([c async for c in device.read_file(afc, f.path, f.size)])
        except Exception:
            await reset()
            raise


async def device_thumb(f: device.DeviceFile, size: int) -> bytes:
    key = hashlib.sha1(f"{f.path}|{int(f.mtime.timestamp())}|{size}".encode()).hexdigest()
    cache = DATA_ROOT / ".devthumbs" / f"{key}.jpg"
    if cache.is_file():
        return cache.read_bytes()
    if f.kind != "photos" or f.size > MAX_BYTES:
        raise ValueError("no preview for this item")
    data = await fetch(f)
    jpg = await asyncio.to_thread(_render, data, size)
    cache.parent.mkdir(exist_ok=True)
    cache.write_bytes(jpg)
    return jpg
