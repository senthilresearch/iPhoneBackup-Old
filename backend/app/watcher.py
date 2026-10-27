"""Background watcher: detects the phone being plugged in/out, optionally auto-imports,
and publishes live state over Server-Sent Events."""
import asyncio
import json
from dataclasses import asdict

from . import device, importer, settings, thumbs
from .catalog import Filter, Selection, catalog

state: dict = {"connected": False, "reason": "NoDevice"}
POLL_SECONDS = 2


def snapshot() -> dict:
    return {"device": state, "import": asdict(importer.progress), "catalog": catalog.meta()}


async def _on_connected() -> None:
    if not catalog.files or catalog.device_id != state["id"]:
        catalog.load_cached(state["id"])  # instant preview from the last scan
    asyncio.create_task(catalog.refresh())
    if settings.load()["auto_import"] and not importer.is_running():
        try:
            importer.start(Selection(filter=Filter(kinds=["photos", "videos"], hide_imported=True)), None)
        except RuntimeError:
            pass


async def _on_disconnected() -> None:
    await thumbs.reset()


async def watch() -> None:
    """Poll usbmux cheaply; do the (slower) lockdown handshake only when the device set changes
    or while the phone isn't ready yet."""
    global state
    last_mux = None
    while True:
        try:
            mux = await device.mux_devices()
            if mux != last_mux or not state.get("connected"):
                last_mux = mux
                new = await device.device_state()
                was, now = state.get("connected"), new.get("connected")
                state = new
                if now and not was:
                    await _on_connected()
                elif was and not now:
                    await _on_disconnected()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            state = {"connected": False, "reason": type(e).__name__}
        await asyncio.sleep(POLL_SECONDS)


async def event_stream():
    last = None
    idle = 0
    while True:
        data = json.dumps(snapshot(), default=str)
        if data != last or idle >= 20:
            yield f"data: {data}\n\n"
            last, idle = data, 0
        else:
            idle += 1
        await asyncio.sleep(0.5)
