"""Test fixtures: a throw-away library folder and a fake iPhone behind the device layer."""
import io
import os
import tempfile
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import pytest

_TMP = tempfile.mkdtemp(prefix="iphone_lib_")
os.environ["IPHONE_LIBRARY"] = _TMP  # must be set before the app is imported

from PIL import Image  # noqa: E402

from app import catalog as catalog_mod  # noqa: E402
from app import device, importer, watcher  # noqa: E402


def jpeg(color, size=(64, 48)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "JPEG")
    return out.getvalue()


def ts(y, m, d, hh=12) -> int:
    return int(datetime(y, m, d, hh, tzinfo=timezone.utc).timestamp())


def make_photos_db(favorites=(), others=("DCIM/100APPLE|OTHER_0001.JPG",)) -> bytes:
    """A minimal stand-in for the phone's Photos.sqlite: ZASSET(ZDIRECTORY, ZFILENAME, ZFAVORITE)."""
    import os
    import sqlite3
    import tempfile
    fd, name = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    con = sqlite3.connect(name)
    con.execute("CREATE TABLE ZASSET (ZDIRECTORY TEXT, ZFILENAME TEXT, ZFAVORITE INTEGER)")
    for path in favorites:
        d, n = path.rsplit("/", 1)
        con.execute("INSERT INTO ZASSET VALUES (?,?,1)", (d.lstrip("/"), n))
    for path in others:
        d, n = path.split("|")
        con.execute("INSERT INTO ZASSET VALUES (?,?,0)", (d, n))
    con.commit()
    con.close()
    data = open(name, "rb").read()
    os.unlink(name)
    return data


class FakeAfc:
    def __init__(self, files: dict[str, tuple[bytes, int]]):
        self.files = files
        self.photos_db: bytes | None = make_photos_db()  # None: the phone doesn't expose it
        self.handles = {}
        self.chunk_delay = 0.0
        self.stat_calls: list[str] = []
        self.opened: list[str] = []

    async def walk(self, root):
        dirs: dict[str, list[str]] = {}
        for p in self.files:
            if p.startswith(root + "/"):
                pp = PurePosixPath(p)
                dirs.setdefault(str(pp.parent), []).append(pp.name)
        if not dirs and root not in ("/DCIM",):
            raise FileNotFoundError(root)
        for d, names in sorted(dirs.items()):
            yield d, [], names

    async def listdir(self, path):
        prefix = path.rstrip("/") + "/"
        names = {p[len(prefix):].split("/")[0] for p in self.files if p.startswith(prefix)}
        if not names and path != "/DCIM":
            raise FileNotFoundError(path)
        return sorted(names)

    async def stat(self, path):
        self.stat_calls.append(path)
        if path in self.files:
            data, mtime = self.files[path]
            return {"st_size": len(data), "st_mtime": mtime, "st_ifmt": "S_IFREG"}
        if any(p.startswith(path + "/") for p in self.files):
            return {"st_size": 0, "st_mtime": 0, "st_ifmt": "S_IFDIR"}
        raise FileNotFoundError(path)

    async def get_file_contents(self, path):
        if path == "/PhotoData/Photos.sqlite" and self.photos_db is not None:
            return self.photos_db
        raise FileNotFoundError(path)

    async def rm_single(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        del self.files[path]

    async def fopen(self, path):
        self.opened.append(path)
        h = len(self.handles) + 1
        self.handles[h] = [self.files[path][0], 0]
        return h

    async def fread(self, h, n):
        import asyncio

        if self.chunk_delay:
            await asyncio.sleep(self.chunk_delay)
        buf = self.handles[h]
        data = buf[0][buf[1] : buf[1] + n]
        buf[1] += len(data)
        return data

    async def fclose(self, h):
        self.handles.pop(h, None)


PHONE_FILES = {
    "/DCIM/100APPLE/IMG_0001.JPG": (jpeg("red"), ts(2026, 3, 5)),
    "/DCIM/100APPLE/IMG_0002.JPG": (jpeg("green"), ts(2026, 3, 20)),
    "/DCIM/100APPLE/IMG_0003.JPG": (jpeg("blue"), ts(2026, 4, 2)),
    "/DCIM/100APPLE/IMG_0003.AAE": (b"<xml/>", ts(2026, 4, 2)),
    "/DCIM/100APPLE/IMG_0004.MOV": (b"\x00" * 3000, ts(2026, 4, 10)),
    "/DCIM/.MISC/hidden.JPG": (jpeg("black"), ts(2026, 1, 1)),
    "/DCIM/100APPLE/notes.txt": (b"ignored", ts(2026, 1, 1)),
    "/Recordings/memo1.m4a": (b"audio" * 100, ts(2026, 3, 8)),
}


@pytest.fixture(autouse=True)
def clean_library():
    """Every test starts with an empty library."""
    import shutil

    from app.library import FOLDERS, KINDS, connect, init_library

    init_library()
    for kind in KINDS:
        with connect(kind) as conn:
            conn.execute("DELETE FROM items")
        for child in (Path(_TMP) / FOLDERS[kind]).iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    for extra in (".tmp", ".thumbs", ".devthumbs", ".cache"):
        shutil.rmtree(Path(_TMP) / extra, ignore_errors=True)
    (Path(_TMP) / ".tmp").mkdir(exist_ok=True)


@pytest.fixture()
def phone(monkeypatch):
    """Connected fake iPhone; returns the FakeAfc so tests can tweak it."""
    afc = FakeAfc(dict(PHONE_FILES))

    @asynccontextmanager
    async def fake_open():
        yield "FAKE-UDID", afc

    monkeypatch.setattr(device, "open_afc", fake_open)
    watcher.state = {"connected": True, "id": "FAKE-UDID", "name": "Test iPhone", "ios": "27", "transport": "usb"}
    catalog_mod.catalog.invalidate()
    importer.progress = importer.Progress()
    yield afc
    watcher.state = {"connected": False, "reason": "NoDevice"}
    catalog_mod.catalog.invalidate()


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    async def idle_watcher():  # don't poll real USB during tests
        import asyncio

        await asyncio.sleep(3600)

    monkeypatch.setattr(watcher, "watch", idle_watcher)
    with TestClient(app) as c:  # keeps one event loop alive so background import tasks run
        yield c


def wait_import(client, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        s = client.get("/api/import/status").json()
        if s["state"] in ("done", "cancelled", "error"):
            return s
        time.sleep(0.05)
    raise AssertionError("import did not finish")


def library_root() -> Path:
    return Path(_TMP)


# ---- the client relay and, optionally, a real PostgreSQL (shared by test_relay.py and test_admin_service.py) ----

class _ExternalPg:
    """A PostgreSQL that is already running (TEST_PG_URI=postgresql://user:pw@host:port/postgres), for machines where pgserver has no wheel (Windows)."""

    def __init__(self, uri: str):
        self._uri = uri

    def get_uri(self) -> str:
        return self._uri

    def cleanup(self) -> None:
        pass


@pytest.fixture(scope="session")
def pg_server(tmp_path_factory):
    pytest.importorskip("psycopg")
    if os.environ.get("TEST_PG_URI"):
        yield _ExternalPg(os.environ["TEST_PG_URI"])
        return
    pgserver = pytest.importorskip("pgserver")
    server = pgserver.get_server(tmp_path_factory.mktemp("pg"))
    yield server
    server.cleanup()


_pg_count = 0


def new_pg_url(request, prefix: str) -> str:
    """A fresh, empty database on the test PostgreSQL server; returns its connection URL."""
    import psycopg

    global _pg_count
    uri = request.getfixturevalue("pg_server").get_uri()
    _pg_count += 1
    name = f"{prefix}_test_{os.getpid()}_{_pg_count}"  # the pid keeps names apart on a server that outlives a run
    with psycopg.connect(uri, autocommit=True) as c:
        c.execute(f"CREATE DATABASE {name}")
    return uri.rsplit("/", 1)[0] + f"/{name}"  # the same server, the new database


@pytest.fixture(params=["sqlite", "postgres"])
def relay(request, tmp_path, monkeypatch):
    """The relay app on SQLite and on a PostgreSQL (a fresh database each; skipped if pgserver is not installed)."""
    from fastapi.testclient import TestClient

    from relay import auth as relay_auth
    from relay import config as relay_config
    from relay.main import app as relay_app

    monkeypatch.setattr(relay_config, "DATA_DIR", tmp_path / "relay")
    monkeypatch.setattr(relay_config, "DATABASE_URL", new_pg_url(request, "relay") if request.param == "postgres" else "")
    relay_auth._hits.clear()  # throttle and lockout state is process-wide; every test starts fresh
    relay_auth._failures.clear()
    with TestClient(relay_app, client=("127.0.0.1", 50000)) as c:
        yield c
