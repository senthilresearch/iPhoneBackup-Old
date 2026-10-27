"""The Mac agent's browser screen, driven over its HTTP API with the fake phone and the real backend."""
import time
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from mac_agent import ui

from .conftest import PHONE_FILES, FakeAfc, library_root
from .test_mac_agent import stored


@pytest.fixture()
def screen(client, monkeypatch, tmp_path):
    afc = FakeAfc(dict(PHONE_FILES))

    @asynccontextmanager
    async def fake_open():
        yield "FAKE-UDID", afc

    async def phone_ok():
        return {"connected": True, "name": "Test iPhone", "ios": "27", "transport": "usb"}

    monkeypatch.setattr(ui, "open_afc", fake_open)
    monkeypatch.setattr(ui, "device_state", phone_ok)
    monkeypatch.setattr(ui, "make_client", lambda *_a: client)  # the real backend app
    monkeypatch.setattr(ui, "CONFIG", tmp_path / "cfg.json")
    monkeypatch.setattr(type(client), "close", lambda self: None)  # the job uses `with client`; keep it open
    c = TestClient(ui.create_app("k"), base_url="http://127.0.0.1", headers={"X-UI-Key": "k"})
    c.afc = afc
    return c


def wait_done(c, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        s = c.get("/api/state").json()
        if not s["running"] and s["phase"] not in ("idle", "starting"):
            return s
        time.sleep(0.05)
    raise AssertionError("sync did not finish")


BODY = {"server": "http://x", "token": "t", "kinds": ["photos", "videos", "files"]}


def test_start_syncs_and_reports(screen):
    assert screen.post("/api/start", json=BODY).status_code == 200
    s = wait_done(screen)
    assert s["phase"] == "done" and s["uploaded"] == 6 and s["failed"] == 0
    assert s["bytes_sent"] == s["total_bytes"] > 0
    assert screen.get("/api/config").json()["server"] == "http://x"  # remembered for next time


def preview(c, **kw):
    assert c.post("/api/preview", json={**BODY, **kw}).status_code == 200
    s = wait_done(c)
    assert s["phase"] == "previewed", s
    return s


def test_preview_lists_files_without_uploading(screen):
    s = preview(screen, kinds=["photos"])
    assert s["matched"] == 4  # 3 jpg + AAE
    page = screen.get("/api/files?offset=0&limit=3").json()
    assert page["total"] == 4 and len(page["items"]) == 3
    assert page["items"][0]["mtime"] >= page["items"][1]["mtime"]  # newest first
    assert stored() == []


def test_date_range_is_inclusive_of_both_days(screen):
    # phone files are dated at noon UTC: 3/5 IMG_0001, 3/8 memo1, 3/20 IMG_0002, 4/2 IMG_0003 + .AAE, 4/10 IMG_0004
    assert preview(screen, since="2026-03-20", until="2026-04-02")["matched"] == 3  # both end days included
    assert preview(screen, until="2026-03-05")["matched"] == 1
    assert preview(screen, since="2026-04-10")["matched"] == 1


def test_start_honours_exclusions_and_reuses_the_scan(screen, monkeypatch):
    preview(screen, kinds=["photos"])
    scans = []
    import mac_agent.ui as ui_mod

    real = ui_mod.scan
    monkeypatch.setattr(ui_mod, "scan", lambda afc: scans.append(1) or real(afc))
    screen.post("/api/start", json={**BODY, "kinds": ["photos"], "exclude": ["/DCIM/100APPLE/IMG_0001.JPG"]})
    s = wait_done(screen)
    assert s["uploaded"] == 3 and not scans  # cached scan reused, one file unticked
    assert "IMG_0001.JPG" not in stored()


def test_start_with_include_only_sends_those(screen):
    preview(screen)
    screen.post("/api/start", json={**BODY, "include": ["/DCIM/100APPLE/IMG_0004.MOV"]})
    assert wait_done(screen)["uploaded"] == 1


def test_thumbnail_for_photo_and_placeholder_for_video(screen, monkeypatch):
    from app import device

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_open():
        yield "FAKE-UDID", screen.afc

    async def no_frame(*_a):
        return None  # QuickLook can't render the fake video bytes

    from mac_agent import videothumb

    monkeypatch.setattr(device, "open_afc", fake_open)
    monkeypatch.setattr(videothumb, "_quicklook", no_frame)
    preview(screen)
    r = screen.get("/api/thumb", params={"path": "/DCIM/100APPLE/IMG_0001.JPG"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    assert screen.get("/api/thumb", params={"path": "/DCIM/100APPLE/IMG_0004.MOV"}).status_code == 404  # video without a renderable frame
    assert screen.get("/api/thumb", params={"path": "/nope.jpg"}).status_code == 404


def test_stop_ends_early(screen):
    screen.afc.chunk_delay = 0.2
    screen.post("/api/start", json=BODY)
    time.sleep(0.5)
    screen.post("/api/stop")
    s = wait_done(screen)
    assert s["phase"] == "stopped" and s["uploaded"] < 6


def test_phone_not_connected_is_an_error_with_hint(screen, monkeypatch):
    async def locked():
        return {"connected": False, "reason": "Locked"}

    monkeypatch.setattr(ui, "device_state", locked)
    screen.post("/api/start", json=BODY)
    s = wait_done(screen)
    assert s["phase"] == "error" and "Unlock" in s["message"]
    assert "Unlock" in screen.get("/api/phone").json()["hint"]


def test_only_this_browser_may_drive_it(screen):
    app = screen.app
    assert TestClient(app, base_url="http://127.0.0.1").get("/api/state").status_code == 403  # no key
    assert TestClient(app, base_url="http://127.0.0.1").get("/api/state?k=k").status_code == 200  # <img> style key
    assert TestClient(app, base_url="http://evil.example", headers={"X-UI-Key": "k"}).get("/").status_code == 403
    page = TestClient(app, base_url="http://127.0.0.1").get("/")
    assert page.status_code == 200 and 'const KEY = "k"' in page.text


def test_pairing_link_fills_server_and_token(screen):
    r = screen.get("/api/pair", params={"link": "http://192.168.0.115:8081/upload.html?token=abc_DEF-1"})
    assert r.json() == {"server": "http://192.168.0.115:8081", "token": "abc_DEF-1"}
    assert screen.get("/api/pair", params={"link": "http://pc:8081/upload.html"}).status_code == 400
    assert screen.get("/api/pair", params={"link": "garbage"}).status_code == 400


def test_discover_returns_token_only_when_server_allows(screen, monkeypatch):
    import httpx

    class R:
        def __init__(self, code, body=None):
            self.status_code, self._b = code, body

        def json(self):
            return self._b

    monkeypatch.setattr(httpx, "get", lambda *_a, **_k: R(200, {"token": "t0k"}))
    assert screen.get("/api/discover", params={"server": "http://127.0.0.1:8765"}).json() == {"token": "t0k"}
    monkeypatch.setattr(httpx, "get", lambda *_a, **_k: R(401))
    assert screen.get("/api/discover", params={"server": "http://pc:8765"}).json() == {"token": None}
    def boom(*_a, **_k):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(httpx, "get", boom)
    assert screen.get("/api/discover", params={"server": "http://pc:8765"}).json() == {"token": None}


def test_state_says_which_operation_ran_and_its_steps(screen):
    s = preview(screen)
    assert s["op"] == "preview" and s["steps"] == ["Check phone", "Read phone", "Check server"] and s["step"] == 3
    # without a server the preview just looks at the phone
    no_server = preview(screen, server="", token="")
    assert no_server["steps"] == ["Check phone", "Read phone", "Check server"] and no_server["skipped_steps"] == [2]  # no server set: skipped
    assert not no_server["existing_checked"]
    assert s["scan_count"] == 6  # files found while reading the phone
    screen.post("/api/start", json=BODY)
    s = wait_done(screen)
    assert s["op"] == "copy" and s["steps"][-1] == "Copy files" and s["step"] == len(s["steps"])
    done = s["uploaded"] + s["duplicates"] + s["skipped"] + s["failed"]
    assert s["total"] == done == 6  # the page's "x of N files" and percent come from these


def test_total_is_known_before_the_first_file_finishes(screen):
    screen.afc.chunk_delay = 0.3
    screen.post("/api/start", json=BODY)
    end = time.time() + 5
    while time.time() < end and screen.get("/api/state").json()["phase"] != "uploading":
        time.sleep(0.02)
    s = screen.get("/api/state").json()
    assert s["phase"] == "uploading" and s["total"] == 6 and s["step"] == 3
    screen.post("/api/stop")
    wait_done(screen)


def test_preview_can_be_cancelled_while_reading_the_phone(screen):
    real_stat = screen.afc.stat

    async def slow_stat(path):
        import asyncio

        await asyncio.sleep(0.2)
        return await real_stat(path)

    screen.afc.stat = slow_stat
    screen.post("/api/preview", json=BODY)
    time.sleep(0.5)
    assert screen.get("/api/state").json()["phase"] == "scanning"
    screen.post("/api/stop")
    s = wait_done(screen)
    assert s["phase"] == "stopped" and s["message"] == "Cancelled."
    assert screen.app.state.job.scan is None  # a cancelled scan must not be cached as complete


def test_video_thumbnail_via_quicklook_with_a_size_cap(screen, monkeypatch):
    import io
    from contextlib import asynccontextmanager

    from PIL import Image

    from app import device
    from mac_agent import videothumb

    @asynccontextmanager
    async def fake_open():
        yield "FAKE-UDID", screen.afc

    async def fake_quicklook(src, size):
        assert src.suffix == ".mov" and src.read_bytes()  # the phone file was copied next to it
        out = io.BytesIO()
        Image.new("RGB", (40, 30), "navy").save(out, "PNG")
        return out.getvalue()

    monkeypatch.setattr(device, "open_afc", fake_open)
    monkeypatch.setattr(videothumb, "_quicklook", fake_quicklook)
    monkeypatch.setattr(videothumb.sys, "platform", "darwin")
    preview(screen)
    r = screen.get("/api/thumb", params={"path": "/DCIM/100APPLE/IMG_0004.MOV"})
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"
    monkeypatch.setattr(videothumb, "MAX_VIDEO_BYTES", 10)
    assert screen.get("/api/thumb", params={"path": "/DCIM/100APPLE/IMG_0004.MOV", "size": 201}).status_code == 404


def test_changing_kinds_rescans_but_remembers_what_it_learned(screen):
    preview(screen, kinds=["photos"])
    screen.afc.stat_calls.clear()
    preview(screen, kinds=["videos"])  # the in-memory scan only covered photos, so the phone is read again...
    assert screen.afc.stat_calls == ["/DCIM/100APPLE/IMG_0004.MOV"]  # ...but only the video is new to us
    screen.afc.stat_calls.clear()
    preview(screen, kinds=["photos"])
    assert screen.afc.stat_calls == []  # photo facts came from the remembered scan
    preview(screen, kinds=["photos"], rescan=True)
    assert screen.afc.stat_calls  # Rescan asks about everything again


def test_preview_reports_the_extensions_found(screen):
    s = preview(screen)
    assert s["extensions"] == [  # most files first
        {"ext": ".jpg", "count": 3, "bytes": s["extensions"][0]["bytes"]},
        {"ext": ".aae", "count": 1, "bytes": 6},
        {"ext": ".m4a", "count": 1, "bytes": 500},
        {"ext": ".mov", "count": 1, "bytes": 3000},
    ]
    assert sum(e["count"] for e in s["extensions"]) == s["matched"]
    # the list follows the date/type filters, so only types that are actually there get a checkbox
    assert [e["ext"] for e in preview(screen, kinds=["videos"])["extensions"]] == [".mov"]


def test_grid_can_be_limited_to_ticked_extensions(screen):
    preview(screen)
    both = screen.get("/api/files", params={"exts": ".jpg,.mov"}).json()
    assert both["total"] == 4 and {i["name"].rsplit(".", 1)[1] for i in both["items"]} == {"JPG", "MOV"}
    assert screen.get("/api/files", params={"exts": ""}).json()["total"] == 0  # "None" ticked
    assert screen.get("/api/files").json()["total"] == 6  # no limit given


def test_start_copies_only_ticked_extensions_and_preview_ignores_them(screen):
    s = preview(screen, exts=[".jpg"])  # a stale selection must not hide types from the list
    assert s["matched"] == 6
    screen.post("/api/start", json={**BODY, "exts": [".jpg", ".m4a"]})
    s = wait_done(screen)
    assert s["uploaded"] == 4 and s["total"] == 4
    assert {"IMG_0001.JPG", "memo1.m4a"} <= set(stored()) and "IMG_0004.MOV" not in stored()


def test_files_without_an_extension_get_a_checkbox_too(screen):
    screen.afc.files["/Downloads/README"] = (b"hi", 1_700_000_000)
    s = preview(screen, kinds=["files"])
    assert {"ext": "(none)", "count": 1, "bytes": 2} in s["extensions"]
    assert screen.get("/api/files", params={"exts": "(none)"}).json()["total"] == 1


def test_preview_marks_files_the_server_already_has(screen):
    screen.post("/api/start", json={**BODY, "kinds": ["photos"]})
    wait_done(screen)
    s = preview(screen)
    assert s["existing_checked"] and s["existing_count"] == 4  # the 3 JPGs + .AAE copied above
    items = screen.get("/api/files", params={"limit": 50}).json()["items"]
    assert {i["name"] for i in items if i["exists"]} == {"IMG_0001.JPG", "IMG_0002.JPG", "IMG_0003.JPG", "IMG_0003.AAE"}
    hidden = screen.get("/api/files", params={"hide_existing": "true"}).json()
    assert hidden["total"] == 2 and not any(i["exists"] for i in hidden["items"])


def test_preview_survives_a_server_that_cannot_be_checked(screen, monkeypatch):
    import httpx

    def down(*_a, **_k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(ui, "check_existing", down)
    s = preview(screen)
    assert s["matched"] == 6 and not s["existing_checked"] and "Could not check" in s["message"]


def test_replace_button_after_a_copy_resends_only_the_skipped_files(screen):
    screen.post("/api/start", json=BODY)
    wait_done(screen)
    (jpg,) = library_root().rglob("IMG_0002.JPG")
    jpg.write_bytes(b"damaged")
    screen.post("/api/start", json=BODY)
    s = wait_done(screen)
    assert s["uploaded"] == 0 and s["skipped"] == 6 and s["skipped_available"] == 6  # the button's count
    assert jpg.read_bytes() == b"damaged"  # skipped, as promised
    assert screen.post("/api/replace_skipped", json={}).status_code == 200
    s = wait_done(screen)
    assert s["uploaded"] == 6 and s["total"] == 6
    (jpg,) = library_root().rglob("IMG_0002.JPG")
    assert jpg.read_bytes() == PHONE_FILES["/DCIM/100APPLE/IMG_0002.JPG"][0]


def test_replace_skipped_needs_something_skipped(screen):
    assert screen.post("/api/replace_skipped", json={}).status_code == 409


def test_replace_existing_from_the_preview_leaves_new_files_alone(screen):
    screen.post("/api/start", json={**BODY, "kinds": ["photos"]})
    wait_done(screen)
    before = stored()
    screen.post("/api/start", json={**BODY, "only_existing": True})  # all kinds, but only already-copied files
    s = wait_done(screen)
    assert s["total"] == 4 and s["uploaded"] == 4
    assert stored() == before  # the video and the recording, never copied, were not added


def test_a_blank_preview_does_not_wipe_the_saved_server_and_token(screen):
    screen.post("/api/start", json={**BODY, "server": "http://pc:8765", "token": "secret"})
    wait_done(screen)
    preview(screen, server="", token="")  # looking at the phone needs neither
    saved = screen.get("/api/config").json()
    assert saved["server"] == "http://pc:8765" and saved["token"] == "secret"


def test_command_line_server_and_token_prefill_the_form(screen, tmp_path):
    ui.save_config(ui.Params(server="http://saved:8765", token="saved-token"))
    plain = TestClient(ui.create_app("k"), base_url="http://127.0.0.1", headers={"X-UI-Key": "k"})
    assert plain.get("/api/config").json()["server"] == "http://saved:8765"
    given = TestClient(ui.create_app("k", {"server": "http://given:8765", "token": ""}), base_url="http://127.0.0.1",
                       headers={"X-UI-Key": "k"})
    cfg = given.get("/api/config").json()
    assert cfg["server"] == "http://given:8765" and cfg["token"] == "saved-token"  # given wins; blank given keeps saved


# ---- delete from the phone ----

def test_delete_needs_the_typed_confirmation_and_a_preview(screen):
    assert screen.post("/api/delete", json={**BODY, "confirm": ""}).status_code == 400
    assert screen.post("/api/delete", json={**BODY, "confirm": "delete"}).status_code == 400  # exact word
    assert screen.post("/api/delete", json={**BODY, "confirm": "DELETE"}).status_code == 409  # nothing previewed yet
    assert len(screen.afc.files) == len(PHONE_FILES)


def test_delete_removes_verified_files_and_updates_the_list(screen):
    screen.post("/api/start", json=BODY)
    wait_done(screen)
    preview(screen)
    screen.afc.opened.clear()
    assert screen.post("/api/delete", json={**BODY, "confirm": "DELETE"}).status_code == 200
    s = wait_done(screen)
    assert s["op"] == "delete" and s["phase"] == "done" and s["steps"][-1] == "Verify & delete"
    assert s["deleted"] == 6 and s["kept"] == 0 and s["failed"] == 0 and s["bytes_freed"] > 0
    assert s["total"] == 6 and s["step"] == len(s["steps"])
    assert screen.afc.opened == []  # this session just sent them and kept their checksums
    assert not [p for p in screen.afc.files if p.startswith(("/DCIM/100APPLE/IMG", "/Recordings"))]
    assert screen.get("/api/files").json()["total"] == 0  # the grid no longer lists what is gone
    assert screen.app.state.job.scan == []


def test_delete_only_touches_previewed_and_ticked_files(screen):
    screen.post("/api/start", json=BODY)
    wait_done(screen)
    preview(screen, kinds=["photos"])  # the video and the recording were copied too, but not previewed
    keep = "/DCIM/100APPLE/IMG_0001.JPG"
    screen.post("/api/delete", json={**BODY, "confirm": "DELETE", "exclude": [keep]})  # request asks for all kinds
    s = wait_done(screen)
    assert s["deleted"] == 3
    assert keep in screen.afc.files and "/DCIM/100APPLE/IMG_0004.MOV" in screen.afc.files
    assert "/Recordings/memo1.m4a" in screen.afc.files


def test_delete_keeps_what_the_server_does_not_have(screen):
    preview(screen)  # nothing was ever copied
    screen.post("/api/delete", json={**BODY, "confirm": "DELETE"})
    s = wait_done(screen)
    assert s["deleted"] == 0 and s["kept"] == 6 and len(s["kept_items"]) == 6
    assert s["kept_items"][0][1] == "not on the server"
    assert len(screen.afc.files) == len(PHONE_FILES)


def test_aae_edit_file_shows_the_thumbnail_of_its_photo(screen, monkeypatch):
    from contextlib import asynccontextmanager

    from app import device
    from mac_agent import videothumb  # noqa: F401  (imported by ui; keep QuickLook out of this test)

    @asynccontextmanager
    async def fake_open():
        yield "FAKE-UDID", screen.afc

    monkeypatch.setattr(device, "open_afc", fake_open)
    preview(screen)
    photo = screen.get("/api/thumb", params={"path": "/DCIM/100APPLE/IMG_0003.JPG"})
    aae = screen.get("/api/thumb", params={"path": "/DCIM/100APPLE/IMG_0003.AAE"})
    assert aae.status_code == 200 and aae.content == photo.content  # same picture: the photo it belongs to
    screen.afc.files["/DCIM/100APPLE/IMG_0099.AAE"] = (b"<xml/>", 1_700_000_000)  # edit file whose photo is gone
    preview(screen, rescan=True)
    assert screen.get("/api/thumb", params={"path": "/DCIM/100APPLE/IMG_0099.AAE"}).status_code == 404


def test_live_photo_edit_file_prefers_the_photo_over_the_video(screen):
    screen.afc.files["/DCIM/100APPLE/IMG_0010.HEIC"] = (b"x", 1_700_000_000)
    screen.afc.files["/DCIM/100APPLE/IMG_0010.MOV"] = (b"x", 1_700_000_000)
    screen.afc.files["/DCIM/100APPLE/IMG_0010.AAE"] = (b"<xml/>", 1_700_000_000)
    preview(screen)
    owner = screen.app.state.job.stems[("/DCIM/100APPLE", "img_0010")]
    assert owner.ext == ".heic"


def test_replace_them_can_also_copy_the_new_files(screen):
    screen.post("/api/start", json={**BODY, "kinds": ["photos"]})  # 4 files are on the server, 2 are new
    wait_done(screen)
    preview(screen)
    # the count the prompt offers next to its "also copy new files" tick
    assert screen.get("/api/files", params={"limit": 1, "hide_existing": "true"}).json()["total"] == 2
    # ticked: force, not only_existing -> everything selected is sent, existing replaced and new added
    screen.post("/api/start", json={**BODY, "force": True, "only_existing": False})
    s = wait_done(screen)
    assert s["total"] == 6 and s["uploaded"] == 6 and s["skipped"] == 0
    assert {"IMG_0004.MOV", "memo1.m4a"} <= set(stored())


def test_delete_keeps_favourites_and_their_companions(screen):
    from .conftest import make_photos_db
    screen.post("/api/start", json=BODY)
    wait_done(screen)
    preview(screen)
    screen.afc.photos_db = make_photos_db(favorites=["/DCIM/100APPLE/IMG_0001.JPG", "/DCIM/100APPLE/IMG_0004.MOV"])
    screen.post("/api/delete", json={**BODY, "confirm": "DELETE"})
    s = wait_done(screen)
    assert s["deleted"] == 4 and s["kept"] == 2
    assert {why for _, why in s["kept_items"]} == {"a Favourite"}
    assert "/DCIM/100APPLE/IMG_0001.JPG" in screen.afc.files and "/DCIM/100APPLE/IMG_0004.MOV" in screen.afc.files
    assert any("2 Favourites found" in line for line in s["log"])


def test_delete_refuses_when_favourites_cannot_be_read(screen):
    screen.post("/api/start", json=BODY)
    wait_done(screen)
    preview(screen)
    screen.afc.photos_db = None
    screen.post("/api/delete", json={**BODY, "confirm": "DELETE"})
    s = wait_done(screen)
    assert s["deleted"] == 0 and len(screen.afc.files) == len(PHONE_FILES)
    assert "Nothing was deleted" in s["message"]


def test_preview_skips_the_server_check_when_it_is_unreachable(screen, monkeypatch):
    import httpx

    def down(*_a):
        return httpx.Client(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down"))),
                            base_url="http://x")

    monkeypatch.setattr(ui, "make_client", down)
    s = preview(screen, kinds=["photos"])
    assert s["matched"] == 4 and s["existing_checked"] is False and s["existing_count"] == 0
    assert s["steps"][-1] == "Check server" and s["skipped_steps"] == [len(s["steps"]) - 1]  # shown, but skipped
    assert any("Cannot reach the server" in line and "Skipping" in line for line in s["log"])


def test_server_status_endpoint(screen, monkeypatch):
    assert screen.get("/api/server", params={"server": "http://x", "token": "t"}).json() == {"ok": True, "reason": ""}
    assert screen.get("/api/server").json()["ok"] is False  # nothing entered
    monkeypatch.setattr(ui, "make_client", lambda *_a: __import__("httpx").Client(
        transport=__import__("httpx").MockTransport(lambda r: __import__("httpx").Response(401)), base_url="http://x"))
    assert "rejected" in screen.get("/api/server", params={"server": "http://x", "token": "bad"}).json()["reason"]


def test_delete_can_skip_the_server_check_only_with_the_stronger_confirmation(screen, monkeypatch):
    import httpx
    preview(screen)  # nothing was ever copied; the server is (now) unreachable
    monkeypatch.setattr(ui, "make_client", lambda *_a: (_ for _ in ()).throw(httpx.ConnectError("down")))
    assert screen.post("/api/delete", json={**BODY, "confirm": "DELETE", "skip_verify": True}).status_code == 400
    assert screen.post("/api/delete", json={**BODY, "confirm": "DELETE UNVERIFIED"}).status_code == 400  # needs the flag
    assert len(screen.afc.files) == len(PHONE_FILES)
    assert screen.post("/api/delete", json={**BODY, "confirm": "DELETE UNVERIFIED", "skip_verify": True}).status_code == 200
    s = wait_done(screen)
    assert s["phase"] == "done" and s["deleted"] == 6 and s["skipped_steps"] == [0]
    assert any("WITHOUT" in line for line in s["log"])
    assert not [p for p in screen.afc.files if p.startswith(("/DCIM/100APPLE/IMG", "/Recordings"))]
