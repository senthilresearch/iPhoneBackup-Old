"""Run the real app against a FAKE iPhone and a throw-away library, to try the UI without hardware.

    cd backend
    .venv\\Scripts\\python -m tests.demo_server        # http://127.0.0.1:8766
"""
import os
import random
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

os.environ.setdefault("IPHONE_LIBRARY", tempfile.mkdtemp(prefix="iphone_demo_"))

import uvicorn  # noqa: E402

from app import device, watcher  # noqa: E402
from tests.conftest import FakeAfc, jpeg  # noqa: E402


def build_phone() -> FakeAfc:
    rng = random.Random(7)
    files = {}
    start = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    for i in range(1, 181):
        when = start + timedelta(days=rng.randint(0, 270), hours=rng.randint(0, 8))
        color = (rng.randint(30, 255), rng.randint(30, 255), rng.randint(30, 255))
        folder = f"/DCIM/{100 + i // 90}APPLE"
        files[f"{folder}/IMG_{i:04d}.JPG"] = (jpeg(color, (800, 600)), int(when.timestamp()))
        if i % 9 == 0:
            files[f"{folder}/IMG_{i:04d}.MOV"] = (os.urandom(2_500_000), int(when.timestamp()))
    files["/Recordings/Voice 001.m4a"] = (os.urandom(300_000), int(start.timestamp()) + 86400 * 40)
    afc = FakeAfc(files)
    afc.chunk_delay = 0.15
    return afc


AFC = build_phone()


@asynccontextmanager
async def fake_open():
    yield "DEMO-UDID", AFC


async def fake_watch():
    watcher.state = {
        "connected": True, "id": "DEMO-UDID", "name": "Demo iPhone", "ios": "27.0", "transport": "usb",
    }
    from app.catalog import catalog

    await watcher._on_connected()
    import asyncio

    while True:
        await asyncio.sleep(3600)


device.open_afc = fake_open
watcher.watch = fake_watch

if __name__ == "__main__":
    from app.main import app

    uvicorn.run(app, host="127.0.0.1", port=8766)
