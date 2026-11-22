"""Playlist cards (pictures, swims, last update) and reading Shokz earphones over Bluetooth."""
import base64
import socket
import struct
import time
from pathlib import Path

import pytest

from swimtunes import covers, shokz
from swimtunes.library import FOLDER_COVER, Library, write_cover
from swimtunes.web import create_app
from test_round6 import tiny_jpeg
from test_swimtunes import add_song


@pytest.fixture
def lib(tmp_path):
    return Library(tmp_path / "lib")


# ---------- protocol (checked against Gadgetbridge's ShokzSupport.encodeCommand) ----------
def test_encode_matches_gadgetbridge():
    m = shokz.encode(*shokz.FIRMWARE)
    assert len(m) == 52
    assert m.hex() == ("a55a2c000100000028005ab7" "01000000" "20000000" "02000000" "04000000" "02000000"
                       "03000000" "04000000" "02000000" "04000000" "00000000")
    assert shokz.crc16_maxim(b"123456789") == 0x44C2      # CRC-16/MAXIM check value


ANSWERS = {shokz.FIRMWARE: b"\0R_13_20241115\0", shokz.BATTERY: b"\0\x07", shokz.MEDIA_SOURCE: b"\0\x01",
           shokz.EQUALIZER: b"\0\x07", shokz.MP3_MODE: b"\0\x01", shokz.VOLUME: b"\0\x08", shokz.LANGUAGE: b"\0\x00"}


class FakeEarphones:
    """An RFCOMM socket to an OpenSwim Pro: answers one question at a time, in small pieces."""
    def __init__(self, silent=False):
        self.out, self.silent, self.closed = bytearray(), silent, False

    def settimeout(self, t):
        pass

    def sendall(self, data):
        buf = bytearray(data)
        for group, code, args in shokz.decode(buf):
            if not self.silent and (group, code) == shokz.MP3_MODE_SET:
                ANSWERS[shokz.MP3_MODE] = b"\0" + args[:1]
                self.out += shokz.encode(0x01, 0x8026, b"\0\0\0\0")
            if not self.silent and (group, code) in ANSWERS:
                self.out += b"\x00\x01" + shokz.encode(group, code | 0x8000, ANSWERS[(group, code)])  # with noise first

    def recv(self, n):
        if not self.out:
            raise socket.timeout()
        chunk, self.out[:] = bytes(self.out[:7]), self.out[7:]
        return chunk

    def close(self):
        self.closed = True


def test_query_reads_everything():
    info = shokz.query(FakeEarphones(), timeout=0.2)
    assert info == {"firmware": "R_13_20241115", "battery": 80, "media_source": "mp3", "equalizer": "swimming",
                    "mp3_mode": "shuffle", "volume": 50, "language": "en"}


def test_switch_shuffle_off():
    old = ANSWERS[shokz.MP3_MODE]
    try:
        info = shokz.read_device("AA:BB:CC:DD:EE:FF", channel=1, opener=lambda *a: FakeEarphones(), mp3_mode="normal")
        assert info["mp3_mode"] == "normal" and info["battery"] == 80
        assert shokz.encode(*shokz.MP3_MODE_SET, bytes([0, 0, 0, 0]))[12:].hex().endswith("26000000" "04000000" "04000000" "00000000")
    finally:
        ANSWERS[shokz.MP3_MODE] = old


def test_bad_crc_is_ignored():
    msg = bytearray(shokz.encode(2, 0x8003, b"\0\x05"))
    msg[-1] ^= 0xFF
    assert shokz.decode(msg) == []


def sdp_record(uuid16, channel):
    def seq(*items):
        body = b"".join(items)
        return bytes([0x35, len(body)]) + body
    uuid = lambda v: b"\x19" + struct.pack(">H", v)
    u16 = lambda v: b"\x09" + struct.pack(">H", v)
    return seq(u16(0x0001), seq(uuid(uuid16)),
               u16(0x0004), seq(seq(uuid(0x0100)), seq(uuid(0x0003), b"\x08" + bytes([channel]))))


def test_channel_from_sdp_record():
    assert shokz.channel_from_sdp(sdp_record(0xFEF0, 5)) == 5
    assert shokz.channel_from_sdp(sdp_record(0x111E, 3)) is None    # hands-free service, not Shokz
    assert shokz.channel_from_sdp(b"\x01\x02") is None


def test_read_device_finds_the_channel():
    opened = []

    def opener(addr, ch, timeout):
        opened.append(ch)
        if ch == 1:
            raise OSError("refused")
        return FakeEarphones(silent=(ch == 2))      # 2 = another service that never answers
    info = shokz.read_device("AA:BB:CC:DD:EE:FF", name="OpenSwim Pro by Shokz", opener=opener, channels=range(1, 6))
    assert info["channel"] == 3 and info["battery"] == 80 and info["firmware"] == "R_13_20241115"
    assert opened == [1, 2, 3]
    opened.clear()
    shokz.read_device("AA:BB:CC:DD:EE:FF", channel=3, opener=opener, channels=range(1, 6))
    assert opened == [3]                             # a known channel is tried first


def test_read_device_explains_failures():
    def refuse(addr, ch, timeout):
        raise OSError("Host is down")
    with pytest.raises(shokz.ShokzError) as e:
        shokz.read_device("AA:BB:CC:DD:EE:FF", opener=refuse, channels=range(1, 3))
    assert e.value.args[0].key == "err.shokz_unreachable"
    with pytest.raises(shokz.ShokzError) as e:
        shokz.read_device("AA:BB:CC:DD:EE:FF", opener=lambda *a: FakeEarphones(silent=True), channels=range(1, 3))
    assert e.value.args[0].key == "err.shokz_no_answer"


def test_play_hours():
    assert shokz.play_hours("OpenSwim Pro by Shokz", "mp3") == 9
    assert shokz.play_hours("OpenSwim Pro by Shokz", "bluetooth") == 6
    assert shokz.play_hours("OpenSwim", None) == 8


def test_bluetooth_endpoints(lib, monkeypatch):
    c = create_app(lib).test_client()
    st = c.get("/api/shokz").get_json()
    assert st["devices"] == [] and st["last"] == {}
    r = c.post("/api/shokz/read", json={}).get_json()
    j = wait(c, r["id"])
    assert j["status"] == "error" and j["log"][-1]["k"] == "err.shokz_none"

    dev = {"name": "OpenSwim Pro by Shokz", "address": "AA:BB:CC:DD:EE:FF", "channel": None}
    monkeypatch.setattr(shokz, "paired_devices", lambda: [dev])
    monkeypatch.setattr(shokz, "can_connect", lambda: True)
    monkeypatch.setattr("swimtunes.web.sys.platform", "linux")
    seen = {}

    def fake_read(address, *, name, channel, mp3_mode=None):
        seen["channel"], seen["mode"] = channel, mp3_mode
        return {"name": name, "address": address, "channel": 4, "battery": 60, "firmware": "R_13", "media_source": "mp3"}
    monkeypatch.setattr(shokz, "read_device", fake_read)
    j = wait(c, c.post("/api/shokz/read", json={"address": dev["address"]}).get_json()["id"])
    assert j["status"] == "done" and j["result"]["battery"] == 60 and j["result"]["play_hours"] == 9
    assert lib.config()["shokz"]["firmware"] == "R_13"
    wait(c, c.post("/api/shokz/read", json={"mp3_mode": "normal"}).get_json()["id"])
    assert seen["channel"] == 4 and seen["mode"] == "normal"    # channel remembered; shuffle switched off
    wait(c, c.post("/api/shokz/read", json={"mp3_mode": "bogus"}).get_json()["id"])
    assert seen["mode"] is None


def wait(c, jid):
    for _ in range(300):
        j = c.get(f"/api/jobs/{jid}").get_json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.02)
    raise AssertionError("job did not finish")


# ---------- playlist cards ----------
def test_folder_stats_for_cards(lib, tmp_path):
    p = add_song(lib, "doomer", "Molchat Doma", "Sudno", seconds=3)
    add_song(lib, "doomer", "Molchat Doma", "Kletka", seconds=3)
    write_cover(p, tiny_jpeg(tmp_path / "c.jpg"))
    d = lib.folder_path("doomer")
    before = d.stat().st_mtime_ns
    lib.set_folder_cover("doomer", b"\xff\xd8fakejpeg")
    assert d.stat().st_mtime_ns == before            # a new picture doesn't count as "updated"
    f = lib.stats()["folders"][0]
    assert f["cover"] and f["pics"] == ["doomer/Molchat Doma - Sudno.mp3"] and f["artists"] == ["Molchat Doma"]
    assert abs(f["updated"] - before / 1e9) < 1 and f["synced"] is None
    assert len(lib.tracks("doomer")) == 2            # the picture is not a song
    lib.rename_folder("doomer", "Doomer wave")
    assert (lib.folder_path("Doomer wave") / FOLDER_COVER).exists()   # the picture moves with the folder


def test_picture_endpoints(lib, tmp_path):
    p = add_song(lib, "mood", "Kino", "Gruppa krovi")
    write_cover(p, tiny_jpeg(tmp_path / "c.jpg"))
    c = create_app(lib).test_client()
    assert c.get("/api/folders/mood/picture").status_code == 404
    # upload a file (any shape: it becomes a 600x600 square)
    r = c.post("/api/folders/mood/picture", data={"file": (open(tmp_path / "c.jpg", "rb"), "c.jpg")},
               content_type="multipart/form-data")
    assert r.status_code == 200
    pic = c.get("/api/folders/mood/picture")
    assert pic.status_code == 200 and pic.data[:2] == b"\xff\xd8"
    # from a song, and a mosaic made in the browser
    assert c.post("/api/folders/mood/picture", json={"song": "mood/Kino - Gruppa krovi.mp3"}).status_code == 200
    data = "data:image/jpeg;base64," + base64.b64encode((tmp_path / "c.jpg").read_bytes()).decode()
    assert c.post("/api/folders/mood/picture", json={"data": data}).status_code == 200
    bad = c.post("/api/folders/mood/picture", json={"data": "data:image/png;base64,AAAA"})
    assert bad.status_code == 400 and bad.get_json()["key"] == "err.bad_picture"
    assert c.post("/api/folders/mood/picture", json={}).get_json()["key"] == "err.no_picture"
    assert c.get("/api/library").get_json()["folders"][0]["cover"]
    ideas = c.get("/api/folders/mood/pictures").get_json()
    assert ideas["songs"] == ["mood/Kino - Gruppa krovi.mp3"] and ideas["web"] == []   # offline in tests
    assert "Kino" in ideas["keywords"]
    c.delete("/api/folders/mood/picture")
    assert c.get("/api/folders/mood/picture").status_code == 404
    assert c.get("/api/folders/nope/pictures").status_code == 404


def test_picture_suggestions_from_deezer(lib):
    add_song(lib, "NieR", "Keiichi Okabe", "Weight of the World")
    calls = []

    def fetch(url):
        calls.append(url)
        if "/search/artist" in url:
            return {"data": [{"name": "Keiichi Okabe", "picture_big": "https://cdn/artist.jpg"}]}
        if "/search/album" in url:
            return {"data": [{"title": "NieR:Automata OST", "cover_big": "https://cdn/album.jpg"}]}
        return {"data": [{"title": "Game music", "picture_xl": "https://cdn/pl.jpg"}]}
    import os
    os.environ.pop("SWIMTUNES_OFFLINE", None)
    try:
        r = covers.suggest(lib.tracks("NieR"), "NieR", fetch=fetch)
    finally:
        os.environ["SWIMTUNES_OFFLINE"] = "1"
    assert [w["kind"] for w in r["web"]] == ["album", "playlist", "artist"]
    assert any("q=NieR" in u for u in calls) and r["keywords"][0] == "NieR"
    assert covers.suggest(lib.tracks("NieR"), "Downloads", fetch=fetch)["keywords"][:1] != ["Downloads"]


def test_sync_remembers_when_folders_were_copied(lib, tmp_path):
    add_song(lib, "doomer", "Molchat Doma", "Sudno")
    dev = tmp_path / "SWIM PRO"
    dev.mkdir()
    c = create_app(lib).test_client()
    j = wait(c, c.post("/api/sync", json={"device": str(dev)}).get_json()["id"])
    assert j["status"] == "done"
    synced = lib.config()["synced"]["doomer"]
    assert time.time() - synced < 60
    assert c.get("/api/library").get_json()["folders"][0]["synced"] == synced
    st = c.get("/api/earphones/folders").get_json()
    assert st["path"] == str(dev) and st["folders"] == {"doomer": "same"}
    assert c.post("/api/config", json={"swim_minutes": "60"}).status_code == 200
    assert lib.config()["swim_minutes"] == 60


# ---------- automatic update at launch ----------
def test_auto_update_at_launch(lib, monkeypatch):
    from swimtunes import updater
    applied = []
    monkeypatch.setattr(updater, "apply", lambda log: applied.append(1) or ["restart"])
    app = create_app(lib)
    c = app.test_client()
    auto = app.config["MAYBE_AUTO_UPDATE"]
    new = {"available": True, "kind": "app", "latest": "v0.9.1"}
    assert auto({"available": False, "kind": "app"}, wait=0) is None
    assert auto(new, wait=0) is None                     # not started as the app (no restart possible)
    stopped = []
    app.config["SHUTDOWN"] = lambda: stopped.append(1)
    jid = auto(new, wait=0)
    assert wait(c, jid)["status"] == "done" and applied == [1]
    assert c.get("/api/version").get_json()["updating"] == jid
    time.sleep(1.2)
    assert stopped == [1] and app.config["RESTART"] == ["restart"]
    assert auto(new, wait=0) is None                     # same version again right after: no update loop
    assert auto({**new, "latest": "v0.9.2"}, wait=0)
    lib.set_config(auto_update=False)
    assert auto({**new, "latest": "v0.9.3"}, wait=0) is None
    assert c.post("/api/config", json={"auto_update": "true"}).status_code == 200 and lib.config()["auto_update"] is True


def test_tray_icon_is_optional():
    from swimtunes import tray
    import sys as _sys
    if _sys.platform != "win32":
        assert tray.start("http://localhost:8765", lambda: None) is None   # only on Windows
    from swimtunes import app as app_mod
    app_mod._error_box("x")          # no-op outside Windows
    app_mod._attach_terminal()
