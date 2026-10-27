"""Run the Mac agent against the fake phone and the real FastAPI app."""
import asyncio

import httpx
import pytest

from app import device
from mac_agent import agent
from mac_agent.agent import AuthError, sync

from .conftest import PHONE_FILES, FakeAfc, library_root


@pytest.fixture()
def afc():
    return FakeAfc(dict(PHONE_FILES))


def run(client, afc, **kw):
    return asyncio.run(sync(client, afc, "FAKE-UDID", log=lambda *_: None, sleep=lambda _s: None, **kw))


def stored():
    root = library_root()
    return sorted(
        p.name for p in root.rglob("*")
        if p.is_file() and p.relative_to(root).parts[0] in ("Photos", "Videos", "Files")
    )


def test_uploads_new_files_then_nothing(client, afc):
    first = run(client, afc)
    assert first.uploaded == 6 and not first.failed  # 3 jpg, AAE, mov, memo
    assert "IMG_0004.MOV" in stored() and "memo1.m4a" in stored()
    second = run(client, afc)
    assert second.uploaded == 0 and second.skipped == first.uploaded


def test_kinds_since_and_dry_run(client, afc):
    dry = run(client, afc, kinds={"videos"}, dry_run=True)
    assert [f.name for f in dry.planned] == ["IMG_0004.MOV"]
    assert stored() == []  # a dry run stores nothing
    from datetime import datetime, timezone

    late = run(client, afc, since=datetime(2026, 4, 1, tzinfo=timezone.utc), kinds={"photos"})
    assert late.uploaded == 2  # IMG_0003.JPG and its .AAE


def test_network_drop_is_retried_and_resumes(client, afc, monkeypatch):
    monkeypatch.setattr(agent, "CHUNK", 1000)
    real_put, calls = client.put, {"n": 0}

    def flaky_put(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:  # chunk lands, but the response is lost
            real_put(*a, **kw)
            raise httpx.ReadTimeout("lost")
        return real_put(*a, **kw)

    monkeypatch.setattr(client, "put", flaky_put)
    s = run(client, afc, kinds={"videos"})
    assert s.uploaded == 1 and not s.failed
    (mov,) = library_root().rglob("IMG_0004.MOV")
    assert mov.read_bytes() == PHONE_FILES["/DCIM/100APPLE/IMG_0004.MOV"][0]


def test_failed_file_does_not_stop_the_run(client, afc, monkeypatch):
    async def boom(*_a, **_k):
        raise IOError("read error")
        yield  # pragma: no cover

    real = agent.read_file
    monkeypatch.setattr(agent, "read_file", lambda a, p, *r: boom() if p.endswith(".MOV") else real(a, p, *r))
    s = run(client, afc)
    assert [p for p, _ in s.failed] == ["/DCIM/100APPLE/IMG_0004.MOV"]
    assert s.uploaded == 5


def test_wrong_token_fails_fast(client, afc, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "LOOPBACK", set())
    bad = httpx.Client(transport=client._transport, base_url="http://testserver", headers={"X-Token": "nope"})
    with pytest.raises(AuthError):
        agent.preflight(bad)
    with pytest.raises(AuthError):
        run(bad, afc)
    assert stored() == []


def test_force_replaces_stored_copy(client, afc):
    run(client, afc, kinds={"videos"})
    (mov,) = library_root().rglob("IMG_0004.MOV")
    mov.write_bytes(b"corrupted")
    assert run(client, afc, kinds={"videos"}).skipped == 1 and mov.read_bytes() == b"corrupted"
    s = run(client, afc, kinds={"videos"}, force=True)
    assert s.uploaded == 1 and s.skipped == 0
    (mov,) = library_root().rglob("IMG_0004.MOV")  # replaced in place, no IMG_0004_1.MOV left behind
    assert mov.read_bytes() == PHONE_FILES["/DCIM/100APPLE/IMG_0004.MOV"][0]
    assert stored().count("IMG_0004.MOV") == 1


def test_scan_skips_sqlite_and_ibooks_internals(afc):
    afc.files.update({
        "/Downloads/downloads.28.sqlitedb-wal": (b"x", 1_700_000_000),
        "/Downloads/downloads.28.sqlitedb": (b"x", 1_700_000_000),
        "/Books/MetadataStore/BookMetadataStore.sqlite-shm": (b"x", 1_700_000_000),
        "/Books/Purchases/EA05.ibooks/META-INF/signatures.xml": (b"x", 1_700_000_000),
        "/Downloads/keep.pdf": (b"x", 1_700_000_000),
    })
    names = {f.name for f in asyncio.run(device.scan(afc))}
    assert "keep.pdf" in names and "memo1.m4a" in names
    assert not [n for n in names if "sqlite" in n or n == "signatures.xml"]


def scan(afc, **kw):
    return asyncio.run(device.scan(afc, **kw))


def test_scan_asks_the_phone_about_each_entry_once(afc):
    scan(afc)
    assert len(afc.stat_calls) == len(set(afc.stat_calls))


def test_scan_skips_work_for_kinds_not_wanted(afc):
    found = scan(afc, kinds={"photos"})
    assert {f.kind for f in found} == {"photos"}
    asked = " ".join(afc.stat_calls)
    assert ".MOV" not in asked and "/Recordings" not in asked  # no round trips for videos or the Files folders


def test_scan_by_files_leaves_dcim_alone(afc):
    found = scan(afc, kinds={"files"})
    assert [f.name for f in found] == ["memo1.m4a"]
    assert not any(c.startswith("/DCIM") for c in afc.stat_calls)


def test_second_scan_only_asks_about_new_files(afc):
    known = {}
    first = scan(afc, known=known)
    afc.stat_calls.clear()
    again = scan(afc, known=known)
    assert afc.stat_calls == [] and [f.path for f in again] == [f.path for f in first]
    afc.files["/DCIM/100APPLE/IMG_0005.JPG"] = (b"new", PHONE_FILES["/DCIM/100APPLE/IMG_0001.JPG"][1])
    del afc.files["/DCIM/100APPLE/IMG_0001.JPG"]
    third = scan(afc, known=known)
    assert afc.stat_calls == ["/DCIM/100APPLE/IMG_0005.JPG"]
    names = {f.name for f in third}
    assert "IMG_0005.JPG" in names and "IMG_0001.JPG" not in names  # additions and deletions are both seen


def test_ibooks_folders_are_not_entered(afc):
    afc.files["/Books/Purchases/A.ibooks/META-INF/sig.xml"] = (b"x", 1_700_000_000)
    afc.files["/Books/Purchases/A.ibooks/book.xhtml"] = (b"x", 1_700_000_000)
    scan(afc)
    assert not any("A.ibooks" in c for c in afc.stat_calls)


def test_normalize_exts():
    from mac_agent.agent import normalize_exts

    assert normalize_exts("heic, .JPG ,none,") == {".heic", ".jpg", "(none)"}
    assert normalize_exts("") == set()


def test_only_the_chosen_extensions_are_copied(client, afc):
    s = run(client, afc, exts={".jpg", ".m4a"})
    assert s.uploaded == 4 and "IMG_0004.MOV" not in stored() and "IMG_0003.AAE" not in stored()


def test_command_line_exts_and_dry_run_lists_file_types(client, afc, monkeypatch, capsys, tmp_path):
    from contextlib import asynccontextmanager

    from mac_agent import cli

    @asynccontextmanager
    async def fake_open():
        yield "FAKE-UDID", afc

    async def phone():
        return {"connected": True}

    from mac_agent import ui

    monkeypatch.setattr(ui, "CONFIG", tmp_path / "cfg.json")  # the command line remembers its server
    monkeypatch.setattr(cli, "open_afc", fake_open)
    monkeypatch.setattr(cli, "wait_for_phone", lambda _t: phone())
    monkeypatch.setattr(cli.httpx, "Client", lambda **_k: client)
    monkeypatch.setattr(type(client), "close", lambda self: None)
    args = cli.parse_args(["--server", "http://x", "--token", "t", "--exts", "JPG,mov", "--dry-run"])
    assert asyncio.run(cli.run(args)) == 0
    out = capsys.readouterr().out
    assert "File types found: .jpg 3, .mov 1" in out and "m4a" not in out
    assert ui.load_config()["server"] == "http://x"  # remembered for the screen


def test_existing_folders_and_files_are_never_replaced(client, afc):
    goa = library_root() / "Photos" / "Trips" / "Goa"
    goa.mkdir(parents=True)
    (goa / "mine.jpg").write_bytes(b"my own photo")
    (goa / "IMG_0001.JPG").write_bytes(b"a different file with the same name")
    inode = goa.stat().st_ino
    s = asyncio.run(sync(client, afc, "FAKE-UDID", log=lambda *_: None, sleep=lambda _s: None,
                         kinds={"photos"}, destination="Trips/Goa"))
    assert s.uploaded == 4 and not s.failed
    assert goa.stat().st_ino == inode  # the folder itself was reused, not recreated
    assert (goa / "mine.jpg").read_bytes() == b"my own photo"
    assert (goa / "IMG_0001.JPG").read_bytes() == b"a different file with the same name"  # name clash: kept
    assert (goa / "IMG_0001_1.JPG").is_file()  # the new file got a free name beside it


def test_force_replaces_the_file_but_keeps_the_folder_and_its_neighbours(client, afc):
    asyncio.run(sync(client, afc, "FAKE-UDID", log=lambda *_: None, sleep=lambda _s: None,
                     kinds={"videos"}, destination="Trips/Goa"))
    folder = library_root() / "Videos" / "Trips" / "Goa"
    (folder / "neighbour.mov").write_bytes(b"keep me")
    asyncio.run(sync(client, afc, "FAKE-UDID", log=lambda *_: None, sleep=lambda _s: None,
                     kinds={"videos"}, destination="Trips/Goa", force=True))
    assert folder.is_dir() and (folder / "neighbour.mov").read_bytes() == b"keep me"
    assert (folder / "IMG_0004.MOV").is_file() and not (folder / "IMG_0004_1.MOV").exists()


def test_check_endpoint_reports_what_this_phone_already_sent(client, afc):
    run(client, afc, kinds={"photos"})
    items = [{"local_id": p, "name": p.rsplit("/", 1)[1]} for p in PHONE_FILES if "/DCIM/100APPLE" in p]
    got = client.post("/api/upload/check", json={"client_id": "mac-FAKE-UDID", "items": items}).json()["exists"]
    assert sorted(got) == sorted(p for p in PHONE_FILES if p.endswith((".JPG", ".AAE")) and "/.MISC/" not in p)
    other = client.post("/api/upload/check", json={"client_id": "mac-OTHER", "items": items}).json()
    assert other["exists"] == []  # another phone's files are not "already there"


def test_second_run_skips_with_one_check_not_a_request_per_file(client, afc, monkeypatch):
    run(client, afc)
    posted = []
    real = client.post
    monkeypatch.setattr(client, "post", lambda url, **kw: posted.append(url) or real(url, **kw))
    s = run(client, afc)
    assert s.skipped == 6 and sorted(s.skipped_paths) == sorted(f.path for f in asyncio.run(device.scan(afc)))
    assert posted == ["/api/upload/check"]  # no /init for any of the six


def test_old_backend_without_check_still_skips_via_init(client, afc, monkeypatch):
    run(client, afc)
    real = client.post

    def post(url, **kw):
        if url == "/api/upload/check":
            return httpx.Response(404)
        return real(url, **kw)

    monkeypatch.setattr(client, "post", post)
    s = run(client, afc)
    assert s.skipped == 6 and s.uploaded == 0


def test_force_does_not_pre_check(client, afc, monkeypatch):
    run(client, afc)
    posted = []
    real = client.post
    monkeypatch.setattr(client, "post", lambda url, **kw: posted.append(url) or real(url, **kw))
    assert run(client, afc, force=True).uploaded == 6
    assert "/api/upload/check" not in posted


# ---- delete from the phone ----

def delete(client, afc, files=None, **kw):
    files = files if files is not None else asyncio.run(device.scan(afc))
    return asyncio.run(agent.delete_from_phone(client, afc, "FAKE-UDID", files, log=lambda *_: None, **{"favorites": set(), **kw}))


def test_delete_removes_only_files_the_server_holds_identically(client, afc):
    run(client, afc, kinds={"photos"})  # photos are on the server; the video and the recording are not
    s = delete(client, afc)
    assert s.deleted == 4 and s.bytes_freed > 0 and not s.failed
    assert sorted(p for p, _ in s.kept) == ["/DCIM/100APPLE/IMG_0004.MOV", "/Recordings/memo1.m4a"]
    assert {why for _, why in s.kept} == {"not on the server"}
    assert "/DCIM/100APPLE/IMG_0001.JPG" not in afc.files and "/DCIM/100APPLE/IMG_0004.MOV" in afc.files
    assert "/DCIM/.MISC/hidden.JPG" in afc.files  # never in the list, so never touched


def test_delete_keeps_files_whose_size_or_content_differs(client, afc):
    run(client, afc, kinds={"videos"})
    mov = "/DCIM/100APPLE/IMG_0004.MOV"
    mtime = afc.files[mov][1]
    afc.files[mov] = (b"\x01" * 3000, mtime)  # same size, different bytes
    (why,) = [w for p, w in delete(client, afc, kinds_only(afc, ".MOV")).kept]
    assert "checksum differs" in why and mov in afc.files
    afc.files[mov] = (b"\x00" * 2999, mtime)  # different size
    (why,) = [w for p, w in delete(client, afc, kinds_only(afc, ".MOV")).kept]
    assert "size differs" in why and mov in afc.files


def kinds_only(afc, suffix):
    return [f for f in asyncio.run(device.scan(afc)) if f.name.endswith(suffix)]


def test_delete_trusts_checksums_from_this_session_instead_of_rereading(client, afc):
    sent = run(client, afc, kinds={"videos"})
    assert len(sent.hashes) == 1
    afc.opened.clear()
    s = delete(client, afc, kinds_only(afc, ".MOV"), hashes=sent.hashes)
    assert s.deleted == 1 and afc.opened == []  # nothing was read off the phone again
    run(client, afc, kinds={"files"})
    afc.opened.clear()
    assert delete(client, afc, kinds_only(afc, ".m4a")).deleted == 1  # no remembered checksum: read and compared
    assert afc.opened == ["/Recordings/memo1.m4a"]


def test_delete_reports_a_refusal_and_carries_on(client, afc, monkeypatch):
    run(client, afc, kinds={"photos"})
    real = afc.rm_single

    async def refuse(path):
        if path.endswith("IMG_0002.JPG"):
            raise PermissionError("protected")
        await real(path)

    monkeypatch.setattr(afc, "rm_single", refuse)
    s = delete(client, afc, [f for f in asyncio.run(device.scan(afc)) if f.kind == "photos"])
    assert s.deleted == 3 and [p for p, _ in s.failed] == ["/DCIM/100APPLE/IMG_0002.JPG"]
    assert "/DCIM/100APPLE/IMG_0002.JPG" in afc.files


def test_delete_can_be_stopped_and_needs_the_server(client, afc):
    run(client, afc, kinds={"photos"})
    s = delete(client, afc, should_stop=lambda: True)
    assert s.stopped and s.deleted == 0 and len(afc.files) == len(PHONE_FILES)
    down = httpx.Client(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down"))),
                        base_url="http://x")
    with pytest.raises(httpx.HTTPError):  # can't prove anything, so nothing is deleted
        asyncio.run(agent.delete_from_phone(down, afc, "FAKE-UDID", asyncio.run(device.scan(afc)), favorites=set(), log=print))
    assert len(afc.files) == len(PHONE_FILES)


def test_server_rejects_a_file_damaged_in_transit(client):
    up = client.post("/api/upload/init", json={"client_id": "c", "local_id": "/x.jpg", "name": "x.jpg", "size": 4}).json()
    client.put(f"/api/upload/{up['upload_id']}", params={"offset": 0}, content=b"abcd")
    bad = client.post(f"/api/upload/{up['upload_id']}/complete", json={"sha256": "0" * 64})
    assert bad.status_code == 409 and "Checksum" in bad.text
    assert client.get(f"/api/upload/{up['upload_id']}").status_code == 404  # forgotten: the sender starts over
    again = client.post("/api/upload/init", json={"client_id": "c", "local_id": "/x.jpg", "name": "x.jpg", "size": 4}).json()
    assert again["status"] == "ready"


# ---- favourites are never deleted ----

def test_favourite_keys_cover_live_photo_video_edit_data_and_edited_version():
    from mac_agent.favorites import key_for, path_key
    fav = key_for("DCIM/100APPLE", "IMG_0001.HEIC")
    for companion in ("IMG_0001.MOV", "IMG_0001.AAE", "IMG_E0001.JPG"):
        assert path_key("/DCIM/100APPLE/" + companion) == fav
    assert path_key("/DCIM/101APPLE/IMG_0001.HEIC") != fav  # other folder
    assert path_key("/DCIM/100APPLE/IMG_0002.HEIC") != fav


def test_delete_never_removes_a_favourite_or_its_companions(client, afc):
    from mac_agent.favorites import key_for
    run(client, afc, kinds={"photos"})
    s = delete(client, afc, kinds_only(afc, ".JPG"), favorites={key_for("DCIM/100APPLE", "IMG_0001.JPG")})
    kept = dict(s.kept)
    assert kept["/DCIM/100APPLE/IMG_0001.JPG"] == "a Favourite" and "/DCIM/100APPLE/IMG_0001.JPG" in afc.files
    assert all(p not in afc.files for p in s.deleted_paths) and "/DCIM/100APPLE/IMG_0001.JPG" not in s.deleted_paths


def test_favourites_are_read_from_the_photos_database(afc):
    from mac_agent.favorites import fetch_favorites, key_for
    from .conftest import make_photos_db
    afc.photos_db = make_photos_db(favorites=["/DCIM/100APPLE/IMG_0001.JPG"])
    assert asyncio.run(fetch_favorites(afc)) == {key_for("DCIM/100APPLE", "IMG_0001.JPG")}


def test_unreadable_photos_database_means_no_delete(afc):
    from mac_agent.favorites import FavoritesUnavailable, fetch_favorites
    from .conftest import make_photos_db
    afc.photos_db = None  # not exposed
    with pytest.raises(FavoritesUnavailable):
        asyncio.run(fetch_favorites(afc))
    afc.photos_db = b"not a database"
    with pytest.raises(FavoritesUnavailable):
        asyncio.run(fetch_favorites(afc))
    afc.photos_db = make_photos_db(others=())  # empty: can't be trusted
    with pytest.raises(FavoritesUnavailable):
        asyncio.run(fetch_favorites(afc))


def test_delete_without_verification_still_keeps_favourites(client, afc):
    from mac_agent.favorites import key_for
    # nothing was ever sent to the server: normally everything would be kept
    assert delete(client, afc, kinds_only(afc, ".JPG")).deleted == 0
    s = delete(client, afc, kinds_only(afc, ".JPG"), verify=False, favorites={key_for("DCIM/100APPLE", "IMG_0001.JPG")})
    assert dict(s.kept) == {"/DCIM/100APPLE/IMG_0001.JPG": "a Favourite"} and s.deleted >= 1
    assert "/DCIM/100APPLE/IMG_0001.JPG" in afc.files


def test_delete_without_verification_needs_no_server(afc):
    s = asyncio.run(agent.delete_from_phone(None, afc, "FAKE-UDID", kinds_only(afc, ".m4a"), favorites=set(),
                                            verify=False, log=lambda *_: None))
    assert s.deleted == 1 and "/Recordings/memo1.m4a" not in afc.files
