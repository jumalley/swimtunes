"""Reading Shokz earphones over Bluetooth (protocol from Gadgetbridge; the earphones are simulated)."""
import socket
import struct

import pytest
from helpers import wait

from swimtunes import shokz
from swimtunes.web import create_app

# ---------- protocol (checked against Gadgetbridge's ShokzSupport.encodeCommand) ----------


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
