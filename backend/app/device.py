"""iPhone access (AFC) via pymobiledevice3, over USB or, as a fallback, Wi-Fi."""
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from pymobiledevice3 import usbmux
from pymobiledevice3.exceptions import (
    ConnectionFailedToUsbmuxdError,
    NoDeviceConnectedError,
    PairingError,
    PasswordRequiredError,
)
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.afc import AfcService

from .config import DCIM, FILE_ROOTS, PHOTO_EXT, SIDECAR_EXT, VIDEO_EXT
from .library import classify


IN_CONTAINER = Path("/.dockerenv").exists()  # a Linux container can never reach Apple's USB service


@dataclass
class DeviceFile:
    path: str
    name: str
    ext: str
    kind: str  # photos | videos | files
    size: int
    mtime: datetime  # timezone-aware UTC


async def mux_devices() -> list[tuple[str, str]]:
    """(udid, 'USB' | 'Network') for every device usbmux can see."""
    try:
        return [(d.serial, d.connection_type) for d in await usbmux.list_devices()]
    except ConnectionFailedToUsbmuxdError:
        return []


def pick_transport(devices: list[tuple[str, str]]) -> str | None:
    if any(t == "USB" for _, t in devices):
        return "USB"
    if devices:
        return "Network"
    return None


async def device_state(transport: str | None = None) -> dict:
    """Connection status for the UI. Never raises; `reason` explains a failure."""
    try:
        devices = await usbmux.list_devices()
    except ConnectionFailedToUsbmuxdError:
        return {"connected": False, "reason": "NoAppleService", "container": IN_CONTAINER}
    transport = transport or pick_transport([(d.serial, d.connection_type) for d in devices])
    if transport is None:
        return {"connected": False, "reason": "NoDevice"}
    try:
        lockdown = await create_using_usbmux(connection_type=transport)
        return {
            "connected": True,
            "id": lockdown.udid,
            "name": lockdown.display_name,
            "ios": lockdown.product_version,
            "transport": "usb" if transport == "USB" else "wifi",
        }
    except PasswordRequiredError:
        return {"connected": False, "reason": "Locked", "transport": transport.lower()}
    except PairingError:
        return {"connected": False, "reason": "NotTrusted", "transport": transport.lower()}
    except NoDeviceConnectedError:
        return {"connected": False, "reason": "NoDevice"}
    except Exception as e:  # noqa: BLE001 - surface anything else as a reason string
        return {"connected": False, "reason": type(e).__name__}


@asynccontextmanager
async def open_afc():
    """Yield (device_id, afc), preferring USB over Wi-Fi."""
    transport = pick_transport(await mux_devices())
    lockdown = await create_using_usbmux(connection_type=transport)
    async with AfcService(lockdown) as afc:
        yield lockdown.udid, afc


def to_utc(value) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(int(value), tz=timezone.utc)


def is_internal(dirpath: str, name: str) -> bool:
    """Apple's own bookkeeping, not user files: SQLite databases (+ -shm/-wal) and .ibooks package contents."""
    lower = name.lower()
    return ".sqlite" in lower or lower.endswith(".ibooks") or any(
        part.lower().endswith(".ibooks") for part in dirpath.split("/")
    )


MEDIA_EXT = PHOTO_EXT | VIDEO_EXT | SIDECAR_EXT  # names like these are files, never folders


async def _walk(afc, dirpath: str, kind_for_ext, kinds, known: dict, out: list, on_file) -> None:
    """One pass over a folder tree: list each folder, and ask the phone about an entry (stat) only when needed.

    `known` maps path -> ("d",) | ("f", size, mtime) from earlier scans; known entries cost no stat at all.
    """
    try:
        names = await afc.listdir(dirpath)
    except Exception:  # noqa: BLE001 - folder missing / not readable on this phone
        return
    for name in names:
        if name in (".", "..", "") or name.startswith(".") or is_internal(dirpath, name):
            continue
        path = f"{dirpath}/{name}"
        ext = PurePosixPath(name).suffix.lower()
        info = known.get(path)
        if info is None:
            if ext in MEDIA_EXT and kinds and kind_for_ext(ext) not in kinds:
                continue  # a video during a photos-only scan: not worth a round trip
            try:
                st = await afc.stat(path)
            except Exception:  # noqa: BLE001 - vanished or unreadable
                continue
            info = ("d",) if st.get("st_ifmt") == "S_IFDIR" else ("f", st["st_size"], int(to_utc(st["st_mtime"]).timestamp()))
            known[path] = info
        if info[0] == "d":
            await _walk(afc, path, kind_for_ext, kinds, known, out, on_file)
            continue
        kind = kind_for_ext(ext)
        if kind and (not kinds or kind in kinds):
            out.append(DeviceFile(path, name, ext, kind, info[1], to_utc(info[2])))
            if on_file:
                on_file(len(out))


async def scan(afc: AfcService, on_file=None, kinds: set[str] | None = None, known: dict | None = None) -> list[DeviceFile]:
    """List importable items: photos/videos under /DCIM, plain files in the other media folders.

    `on_file(count)` reports progress. `kinds` limits the work to those kinds (the phone is not asked about the
    rest). `known` is a path -> facts memo that is filled in; pass the same dict next time and only new files
    cost a round trip (a changed-in-place file keeps its old size/date until a scan without `known`).
    """
    known = {} if known is None else known
    found: list[DeviceFile] = []
    if not kinds or kinds & {"photos", "videos"}:
        await _walk(afc, DCIM, classify, kinds, known, found, on_file)
    if not kinds or "files" in kinds:
        for root in FILE_ROOTS:
            await _walk(afc, root, lambda _e: "files", kinds, known, found, on_file)
    return found


async def read_file(afc: AfcService, path: str, size: int, chunk: int = 1 << 20):
    """Async generator of file chunks."""
    handle = await afc.fopen(path)
    try:
        remaining = size
        while remaining > 0:
            data = await afc.fread(handle, min(chunk, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data
        if remaining:
            raise IOError(f"short read: {remaining} bytes missing")
    finally:
        await afc.fclose(handle)
