import hashlib
from datetime import date

import pytest

from app import library
from tests.conftest import jpeg, library_root, wait_import


def rescan(client):
    from app.catalog import catalog
    import asyncio

    asyncio.run(catalog.refresh())


@pytest.fixture()
def scanned(phone, client):
    rescan(client)
    return client


def files(client, **params):
    return client.get("/api/device/files", params=params).json()


def test_scan_lists_media_only_and_hides_sidecars_and_hidden_folders(scanned):
    names = {i["name"] for i in files(scanned, kind=["photos", "videos", "files"])["items"]}
    assert names == {"IMG_0001.JPG", "IMG_0002.JPG", "IMG_0003.JPG", "IMG_0004.MOV", "memo1.m4a"}


def test_kind_filter_is_multi_select(scanned):
    assert files(scanned, kind=["photos"])["total"] == 3
    assert files(scanned, kind=["videos"])["total"] == 1
    assert files(scanned, kind=["photos", "videos"])["total"] == 4
    assert files(scanned, kind=["files"])["total"] == 1


def test_date_range_filter(scanned):
    got = files(scanned, kind=["photos", "videos"], date_from="2026-03-15", date_to="2026-04-05")
    assert {i["name"] for i in got["items"]} == {"IMG_0002.JPG", "IMG_0003.JPG"}


def test_phone_preview_is_a_jpeg(scanned):
    r = scanned.get("/api/device/thumb", params={"path": "/DCIM/100APPLE/IMG_0001.JPG"})
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"
    assert scanned.get("/api/device/thumb", params={"path": "/DCIM/100APPLE/IMG_0004.MOV"}).status_code == 415


def test_import_filter_to_new_folder_with_sidecar(scanned):
    r = scanned.post("/api/folders", json={"path": "Trips/Spring 2026"})
    assert r.status_code == 200
    body = {
        "filter": {"kinds": ["photos"], "date_from": "2026-04-01", "date_to": "2026-04-30"},
        "destination": "Trips/Spring 2026",
    }
    assert scanned.post("/api/import", json=body).status_code == 200
    s = wait_import(scanned)
    assert s["state"] == "done" and s["copied"] == 2  # IMG_0003.JPG + its .AAE
    assert s["bytes_done"] == s["bytes_total"] > 0
    stored = library_root() / "Photos" / "Trips" / "Spring 2026"
    assert sorted(p.name for p in stored.iterdir()) == ["IMG_0003.AAE", "IMG_0003.JPG"]
    lib = scanned.get("/api/library", params={"folder": "Trips"}).json()
    assert [i["name"] for i in lib["items"]] == ["IMG_0003.JPG"]  # sidecar hidden
    assert lib["items"][0]["folder"] == "Trips/Spring 2026"


def test_import_selected_paths_defaults_to_year_month(scanned):
    body = {"paths": ["/DCIM/100APPLE/IMG_0001.JPG", "/DCIM/100APPLE/IMG_0004.MOV"]}
    scanned.post("/api/import", json=body)
    s = wait_import(scanned)
    assert s["copied"] == 2
    assert (library_root() / "Photos" / "2026" / "March" / "IMG_0001.JPG").is_file()
    assert any((library_root() / "Videos" / "2026").rglob("IMG_0004.MOV"))


def test_select_all_with_exclude_and_reimport_skips(scanned):
    body = {"filter": {"kinds": ["photos"]}, "exclude": ["/DCIM/100APPLE/IMG_0002.JPG"]}
    scanned.post("/api/import", json=body)
    assert wait_import(scanned)["copied"] == 3  # 0001, 0003, 0003.AAE
    scanned.post("/api/import", json={"filter": {"kinds": ["photos"]}})
    s = wait_import(scanned)
    assert s["copied"] == 1 and s["skipped"] == 3  # only 0002 is new
    flagged = {i["name"]: i["imported"] for i in files(scanned, kind=["photos"])["items"]}
    assert all(flagged.values())
    assert files(scanned, kind=["photos"], hide_imported=True)["total"] == 0


def test_identical_content_is_deduplicated(scanned, phone):
    phone.files["/DCIM/100APPLE/IMG_0099.JPG"] = (phone.files["/DCIM/100APPLE/IMG_0001.JPG"][0], 1_700_000_000)
    rescan(scanned)
    scanned.post("/api/import", json={"filter": {"kinds": ["photos"]}})
    s = wait_import(scanned)
    assert s["duplicates"] == 1
    assert not list(library_root().rglob("IMG_0099.JPG"))


def test_cancel_stops_import_and_leaves_no_partial_files(scanned, phone):
    phone.chunk_delay = 0.3
    phone.files["/DCIM/100APPLE/BIG.MOV"] = (b"\x01" * (6 * 1024 * 1024), 1_700_000_000)
    rescan(scanned)
    scanned.post("/api/import", json={"paths": ["/DCIM/100APPLE/BIG.MOV"]})
    import time

    time.sleep(0.4)
    scanned.post("/api/import/cancel")
    s = wait_import(scanned)
    assert s["state"] == "cancelled" and s["copied"] == 0
    assert not list((library_root() / ".tmp").glob("*.part"))
    assert not list(library_root().rglob("BIG.MOV"))


def test_pause_and_resume(scanned, phone):
    import time

    phone.chunk_delay = 0.3
    phone.files["/DCIM/100APPLE/BIG.MOV"] = (b"\x01" * (4 * 1024 * 1024), 1_700_000_000)
    rescan(scanned)
    scanned.post("/api/import", json={"paths": ["/DCIM/100APPLE/BIG.MOV"]})
    time.sleep(0.4)
    scanned.post("/api/import/pause")
    time.sleep(0.5)  # let the chunk already in flight land
    a = scanned.get("/api/import/status").json()
    time.sleep(0.5)
    b = scanned.get("/api/import/status").json()
    assert a["state"] == "paused" and a["bytes_done"] == b["bytes_done"]
    scanned.post("/api/import/resume")
    assert wait_import(scanned, 20)["copied"] == 1


def test_import_requires_connected_phone(client):
    assert client.post("/api/import", json={}).status_code == 409


def test_folder_validation(client):
    for bad in ["..", "a/../b", "C:\\x", "con", "a" * 61, "/".join("d" * 1 for _ in range(7))]:
        r = client.post("/api/folders", json={"path": bad})
        assert r.status_code == 400, bad
    assert client.post("/api/folders", json={"path": "Good/Name"}).status_code == 200
    assert "Good/Name" in client.get("/api/folders").json()["folders"]
    assert client.delete("/api/folders", params={"path": "Good/Name"}).status_code == 200
    assert "Good/Name" not in client.get("/api/folders").json()["folders"]


def test_folder_delete_refuses_non_empty(client):
    client.post("/api/folders", json={"path": "Keep"})
    (library_root() / "Photos" / "Keep" / "x.jpg").write_bytes(b"x")
    assert client.delete("/api/folders", params={"path": "Keep"}).status_code == 400


def test_import_rejects_bad_destination(scanned):
    r = scanned.post("/api/import", json={"filter": {"kinds": ["photos"]}, "destination": "../escape"})
    assert r.status_code == 400


# ---- iPhone-side uploads and pairing ---------------------------------------------------------

def test_upload_resumable_and_idempotent(client):
    data = jpeg("yellow", (200, 100))
    init = {
        "client_id": "iphone1", "local_id": "L1", "name": "up.jpg", "size": len(data),
        "taken_at": "2026-05-01T10:00:00Z", "destination": "Uploads",
    }
    up = client.post("/api/upload/init", json=init).json()
    uid, half = up["upload_id"], len(data) // 2
    assert client.put(f"/api/upload/{uid}", params={"offset": 0}, content=data[:half]).json()["received"] == half
    assert client.put(f"/api/upload/{uid}", params={"offset": 0}, content=data[:half]).status_code == 409
    assert client.get(f"/api/upload/{uid}").json()["received"] == half
    client.put(f"/api/upload/{uid}", params={"offset": half}, content=data[half:])
    assert client.post(f"/api/upload/{uid}/complete").json()["status"] == "copied"
    stored = library_root() / "Photos" / "Uploads" / "up.jpg"
    assert hashlib.sha256(stored.read_bytes()).digest() == hashlib.sha256(data).digest()
    assert client.post("/api/upload/init", json=init).json()["status"] == "exists"


def test_upload_incomplete_is_rejected(client):
    init = {"client_id": "iphone1", "local_id": "L2", "name": "a.pdf", "size": 10}
    uid = client.post("/api/upload/init", json=init).json()["upload_id"]
    client.put(f"/api/upload/{uid}", params={"offset": 0}, content=b"12345")
    assert client.post(f"/api/upload/{uid}/complete").status_code == 409


def test_lan_clients_need_the_token(client):
    from fastapi.testclient import TestClient

    from app import settings
    from app.main import app

    lan = TestClient(app, client=("192.168.1.50", 5000))
    assert lan.get("/api/device").status_code == 401
    token = settings.load()["token"]
    assert lan.get("/api/device", headers={"X-Token": token}).status_code == 200
    assert lan.get("/api/device", params={"token": "wrong"}).status_code == 401
    assert lan.get("/api/pairing", headers={"X-Token": token}).status_code == 403  # PC only
    assert lan.put("/api/settings", json={"auto_import": True}, headers={"X-Token": token}).status_code == 403


def test_token_is_admin_in_docker_mode(client, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main, settings

    monkeypatch.setattr(main, "TOKEN_IS_ADMIN", True)
    lan = TestClient(main.app, client=("172.20.0.1", 5000))
    token = settings.load()["token"]
    assert lan.get("/api/pairing").status_code == 401  # still needs the token
    assert lan.get("/api/pairing", headers={"X-Token": token}).status_code == 200


def test_pairing_and_settings_on_this_pc(client):
    p = client.get("/api/pairing").json()
    assert p["upload_url"].endswith(f"/upload.html?token={p['token']}")
    assert client.put("/api/settings", json={"auto_import": True}).json() == {"auto_import": True}
    assert client.get("/api/device").json()["auto_import"] is True
    client.put("/api/settings", json={"auto_import": False})


def test_auto_import_on_connect(phone, monkeypatch):
    import asyncio

    from app import importer, settings, watcher
    from app.catalog import catalog

    settings.update(auto_import=True)
    try:
        async def go():
            await watcher._on_connected()
            for _ in range(200):
                await asyncio.sleep(0.05)
                if importer.progress.state in ("done", "error"):
                    break

        asyncio.run(go())
        assert importer.progress.state == "done" and importer.progress.copied > 0
    finally:
        settings.update(auto_import=False)


def test_old_database_is_migrated(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.executescript(
        "CREATE TABLE items (id INTEGER PRIMARY KEY, device_id TEXT, device_path TEXT, stored_path TEXT,"
        " name TEXT, ext TEXT, size INTEGER, sha256 TEXT, taken_at TEXT, imported_at TEXT, is_sidecar INTEGER);"
        "INSERT INTO items VALUES (1,'d','/p','Photos\\2026\\04\\a.heic','a.heic','.heic',1,'h','t','i',0);"
    )
    library._migrate(con)
    assert con.execute("SELECT folder FROM items").fetchone()[0] == "2026/04"
    library._migrate(con)  # idempotent


def test_settings_file_can_live_outside_the_library(tmp_path, monkeypatch):
    from app import settings

    target = tmp_path / "config" / "settings.json"
    monkeypatch.setattr(settings, "SETTINGS_FILE", target)
    token = settings.load()["token"]  # created on first use, including the missing parent folder
    assert token and target.exists()
    assert settings.load()["token"] == token


def test_metadata_can_live_apart_from_the_media(tmp_path, monkeypatch):
    from app import library

    data = tmp_path / "data"
    monkeypatch.setattr(library, "DATA_ROOT", data)
    library.init_library()
    assert (data / "db" / "photos.db").exists()  # catalogs go to the data folder...
    assert not (data / "Photos").exists()  # ...media folders do not
