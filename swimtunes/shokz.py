"""Read battery, firmware and settings from Shokz earphones over Bluetooth.

The protocol comes from Gadgetbridge's open-source Shokz support
(https://gadgetbridge.org/gadgets/headphones/shokz/), which works with the
OpenSwim Pro without the Shokz app. The earphones expose a Bluetooth Classic
serial (RFCOMM) service with UUID 0xFEF0. Every message is little-endian:

    a5 5a  len16  01 00 00 00  plen16  crc16 | payload
    payload = u32 1, u32 n, u32 2, u32 4, u32 group, u32 3, u32 4, u32 code, u32 4, u32 argc, args

crc16 is CRC-16/MAXIM over the payload. Answers use code | 0x8000.

The earphones must be paired with this computer once (in the computer's
Bluetooth settings), switched on and not in the charging cradle. Works on
Windows and Linux; on a Mac only what macOS itself reports is shown.
"""

from __future__ import annotations

import json
import os
import re
import socket
import struct
import subprocess
import sys
import time
from typing import Callable

from .i18n import msg

SERVICE_UUID16 = 0xFEF0
PREAMBLE = b"\xa5\x5a"
NAME_RE = re.compile(r"openswim|shokz|aftershokz|openrun|openmove|opencomm|openfit|opendots|xtrainerz", re.I)

# (group, code) of the requests; answers are (group, code | 0x8000)
FIRMWARE = (0x02, 0x0002)
BATTERY = (0x02, 0x0003)
VOLUME = (0x02, 0x0007)
EQUALIZER = (0x02, 0x0008)
LANGUAGE = (0x02, 0x0006)
MEDIA_SOURCE = (0x02, 0x001C)
MP3_MODE = (0x02, 0x001D)

EQUALIZERS = {0x01: "standard", 0x02: "vocal", 0x03: "bass", 0x04: "treble", 0x05: "custom",
              0x07: "swimming", 0x08: "classic", 0x09: "privacy", 0x0A: "volume_boost"}
MP3_MODES = {0x00: "normal", 0x01: "shuffle", 0x02: "repeat"}
SOURCES = {0x00: "bluetooth", 0x01: "mp3"}
LANGUAGES = {0x00: "en", 0x01: "zh", 0x02: "ja", 0x03: "ko"}

# Hours of music on a full charge (Shokz specifications), for "swims left".
PLAY_HOURS = {"openswim pro": {"mp3": 9, "bluetooth": 6}, "openswim": {"mp3": 8, "bluetooth": 8}}


class ShokzError(Exception):
    pass


# ---------- protocol ----------
def crc16_maxim(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


def encode(group: int, code: int, args: bytes = b"") -> bytes:
    body = struct.pack("<8I", 0x02, 0x04, group, 0x03, 0x04, code, 0x04, len(args)) + args
    payload = struct.pack("<II", 0x01, len(body)) + body
    head = struct.pack("<HBBHH", len(payload) + 4, 0x01, 0x00, 0x0000, len(payload))
    return PREAMBLE + head + struct.pack("<H", crc16_maxim(payload)) + payload


def decode(buf: bytearray) -> list[tuple[int, int, bytes]]:
    """Take every complete message out of buf; returns [(group, code, args)]."""
    out = []
    while len(buf) >= 12:
        if buf[:2] != PREAMBLE:
            i = buf.find(PREAMBLE, 1)
            del buf[: i if i > 0 else len(buf) - 1]
            continue
        (plen,) = struct.unpack_from("<H", buf, 8)
        if len(buf) < 12 + plen:
            break
        (crc,) = struct.unpack_from("<H", buf, 10)
        payload = bytes(buf[12:12 + plen])
        del buf[:12 + plen]
        if crc != crc16_maxim(payload) or len(payload) < 40:
            continue
        one, n, two, four, group, three, four2, code, four3, argc = struct.unpack_from("<10I", payload)
        if one != 1 or n != len(payload) - 8:
            continue
        out.append((group, code, payload[40:40 + argc]))
    return out


def parse(answers: dict[tuple[int, int], bytes]) -> dict:
    """Turn the answers into readable information."""
    info: dict = {}

    def arg(req):
        a = answers.get((req[0], req[1] | 0x8000))
        return a if a and len(a) >= 2 and a[0] == 0 else None

    if (a := arg(FIRMWARE)) is not None:
        info["firmware"] = a[1:].split(b"\0", 1)[0].decode("ascii", "replace").strip() or None
    if (a := arg(BATTERY)) is not None and a[1] <= 9:
        info["battery"] = (a[1] + 1) * 10           # the earphones report it in steps of 10 %
    if (a := arg(VOLUME)) is not None:
        info["volume"] = min(100, round(a[1] / 16 * 100))
    if (a := arg(MEDIA_SOURCE)) is not None:
        info["media_source"] = SOURCES.get(a[1])
    if (a := arg(EQUALIZER)) is not None:
        info["equalizer"] = EQUALIZERS.get(a[1])
    if (a := arg(MP3_MODE)) is not None:
        info["mp3_mode"] = MP3_MODES.get(a[1])
    if (a := arg(LANGUAGE)) is not None:
        info["language"] = LANGUAGES.get(a[1])
    return {k: v for k, v in info.items() if v is not None}


def _ask(sock, buf: bytearray, answers: dict, group: int, code: int, args: bytes = b"", *,
         expect: tuple[int, int] | None = None, timeout: float = 2.5) -> bool:
    """Send one message and wait for its answer (by default: same group, code | 0x8000)."""
    expect = expect or (group, code | 0x8000)
    sock.sendall(encode(group, code, args))
    end = time.monotonic() + timeout
    while expect not in answers and time.monotonic() < end:
        sock.settimeout(max(0.05, end - time.monotonic()))
        try:
            chunk = sock.recv(2048)
        except socket.timeout:
            break
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
        for g, c, a in decode(buf):
            answers[(g, c)] = a
    return expect in answers


def query(sock, requests=(FIRMWARE, BATTERY, MEDIA_SOURCE, EQUALIZER, MP3_MODE, VOLUME, LANGUAGE),
          timeout: float = 2.5) -> dict:
    """Ask one question at a time (the earphones ignore questions sent together)."""
    buf, answers = bytearray(), {}
    for group, code in requests:
        if not _ask(sock, buf, answers, group, code, timeout=timeout) and (group, code) == FIRMWARE:
            break    # not a Shokz channel (or the earphones are busy)
    return parse(answers)


MP3_MODE_SET = (0x01, 0x0026)   # answered with (0x01, 0x8026)


def set_mp3_mode(sock, mode: str, timeout: float = 3.0) -> str | None:
    """Switch MP3 playback to normal / shuffle / repeat. Returns the mode the earphones now report."""
    code = {v: k for k, v in MP3_MODES.items()}[mode]
    buf, answers = bytearray(), {}
    _ask(sock, buf, answers, *MP3_MODE_SET, bytes([code, 0, 0, 0]), timeout=timeout)
    _ask(sock, buf, answers, *MP3_MODE, timeout=timeout)
    return parse(answers).get("mp3_mode")


# ---------- SDP: which RFCOMM channel the 0xFEF0 service is on ----------
def _sdp_element(data: bytes, i: int):
    """Parse one SDP data element; returns (value, next_index). Sequences become lists,
    UUIDs become ('uuid', int)."""
    d = data[i]
    typ, size = d >> 3, d & 7
    i += 1
    if typ == 0:
        return None, i
    if typ in (1, 2, 3, 5) and size <= 4:
        n = 1 << size
        if typ == 5:
            return bool(data[i]), i + 1
        v = int.from_bytes(data[i:i + n], "big", signed=(typ == 2))
        return (("uuid", v) if typ == 3 else v), i + n
    if size <= 4:
        raise ValueError("bad size")
    n_len = {5: 1, 6: 2, 7: 4}[size]
    n = int.from_bytes(data[i:i + n_len], "big")
    i += n_len
    if typ in (6, 7):
        items, end = [], i + n
        while i < end:
            v, i = _sdp_element(data, i)
            items.append(v)
        return items, end
    return bytes(data[i:i + n]), i + n


def _uuid16(u) -> int | None:
    if isinstance(u, tuple) and u[0] == "uuid":
        v = u[1]
        return (v >> 96) & 0xFFFF if v > 0xFFFFFFFF else v & 0xFFFF
    return None


def channel_from_sdp(record: bytes) -> int | None:
    """RFCOMM channel of the 0xFEF0 service in one SDP service record (or None)."""
    try:
        attrs, _ = _sdp_element(record, 0)
    except (ValueError, IndexError, KeyError):
        return None
    if not isinstance(attrs, list):
        return None
    pairs = dict(zip(attrs[0::2], attrs[1::2]))
    classes = pairs.get(0x0001) or []
    if SERVICE_UUID16 not in {_uuid16(u) for u in classes if not isinstance(u, list)}:
        return None
    for proto in pairs.get(0x0004) or []:
        if isinstance(proto, list) and len(proto) >= 2 and _uuid16(proto[0]) == 0x0003:   # RFCOMM
            return proto[1] if isinstance(proto[1], int) else None
    return None


# ---------- finding the paired earphones ----------
def _fmt_addr(raw: str) -> str:
    h = re.sub(r"[^0-9a-fA-F]", "", raw).upper().rjust(12, "0")
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


def _windows_paired() -> list[dict]:
    import winreg
    out = []
    base = r"SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Devices"
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
    except OSError:
        return out
    i = 0
    while True:
        try:
            mac = winreg.EnumKey(root, i)
        except OSError:
            break
        i += 1
        try:
            k = winreg.OpenKey(root, mac)
            name = winreg.QueryValueEx(k, "Name")[0]
            name = name.split(b"\0", 1)[0].decode("utf-8", "replace") if isinstance(name, bytes) else str(name)
        except OSError:
            continue
        channel = None
        try:   # Windows keeps the earphones' service list (SDP records) here
            ck = winreg.OpenKey(k, "CachedServices")
            j = 0
            while channel is None:
                try:
                    _, rec, _ = winreg.EnumValue(ck, j)
                except OSError:
                    break
                j += 1
                if isinstance(rec, bytes):
                    channel = channel_from_sdp(rec)
        except OSError:
            pass
        out.append({"name": name, "address": _fmt_addr(mac), "channel": channel})
    return out


def _linux_paired() -> list[dict]:
    out = []
    try:
        r = subprocess.run(["bluetoothctl", "devices", "Paired"], capture_output=True, text=True, timeout=8)
        lines = r.stdout.splitlines()
        if not lines:   # older bluetoothctl
            r = subprocess.run(["bluetoothctl", "paired-devices"], capture_output=True, text=True, timeout=8)
            lines = r.stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return out
    for line in lines:
        m = re.match(r"Device\s+([0-9A-Fa-f:]{17})\s+(.*)", line.strip())
        if m:
            out.append({"name": m.group(2), "address": m.group(1).upper(), "channel": None})
    return out


def _mac_devices() -> list[dict]:
    """macOS: Python can't open Bluetooth serial channels there, but macOS knows
    the battery level of some headphones."""
    try:
        r = subprocess.run(["system_profiler", "SPBluetoothDataType", "-json"], capture_output=True, text=True, timeout=20)
        data = json.loads(r.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    out = []
    for ctl in data.get("SPBluetoothDataType", []):
        for group in ("device_connected", "device_not_connected", "devices_list"):
            for entry in ctl.get(group, []) or []:
                for name, d in (entry.items() if isinstance(entry, dict) else []):
                    if not isinstance(d, dict):
                        continue
                    bat = d.get("device_batteryLevelMain") or d.get("device_batteryLevel")
                    out.append({"name": name, "address": _fmt_addr(d.get("device_address", "")),
                                "channel": None, "connected": group == "device_connected",
                                "firmware": d.get("device_firmwareVersion"),
                                "battery": int(str(bat).rstrip("%")) if bat and str(bat).rstrip("%").isdigit() else None})
    return out


def paired_devices() -> list[dict]:
    """The Shokz earphones paired with this computer: [{name, address, channel}]."""
    if os.environ.get("SWIMTUNES_OFFLINE"):
        return []
    try:
        if sys.platform == "win32":
            devs = _windows_paired()
        elif sys.platform == "darwin":
            devs = _mac_devices()
        else:
            devs = _linux_paired()
    except Exception:
        devs = []
    return [d for d in devs if NAME_RE.search(d["name"] or "")]


def can_connect() -> bool:
    return hasattr(socket, "AF_BLUETOOTH") and hasattr(socket, "BTPROTO_RFCOMM")


def _windows_battery(name: str) -> int | None:
    """Fallback: the battery level Windows itself shows (Settings > Bluetooth)."""
    ps = ("Get-PnpDevice -FriendlyName '*" + name.replace("'", "") + "*' -ErrorAction SilentlyContinue | "
          "Get-PnpDeviceProperty -KeyName '{104EA319-6EE2-4701-BD47-8DDBF425BBE5} 2' -ErrorAction SilentlyContinue | "
          "Where-Object Data -ne $null | Select-Object -First 1 -ExpandProperty Data")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        v = r.stdout.strip().splitlines()
        return int(v[0]) if v and v[0].isdigit() else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _open(address: str, channel: int, timeout: float):
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    s.settimeout(timeout)
    try:
        s.connect((address, channel))
    except BaseException:
        s.close()
        raise
    return s


def read_device(address: str, *, name: str = "", channel: int | None = None,
                log: Callable[[str], None] = lambda _: None, opener=_open,
                channels=range(1, 31), mp3_mode: str | None = None) -> dict:
    """Connect to the earphones and read everything (after switching the MP3 playback
    mode first, if mp3_mode is given). Returns
    {name, address, channel, firmware, battery, media_source, equalizer, mp3_mode, volume, language}."""
    info = {"name": name, "address": address}
    tried_any = False
    candidates = [channel] if channel else []
    candidates += [c for c in channels if c != channel]
    last_error = None
    for ch in candidates:
        try:
            s = opener(address, ch, 6.0 if ch == channel else 3.0)
        except OSError as e:
            last_error = e
            continue
        tried_any = True
        try:
            got = query(s, timeout=2.5 if ch == channel else 1.5)
            if mp3_mode and got.get("firmware"):
                got["mp3_mode"] = set_mp3_mode(s, mp3_mode) or got.get("mp3_mode")
        except OSError as e:
            last_error = e
            got = {}
        finally:
            try:
                s.close()
            except OSError:
                pass
        if got.get("firmware") or got.get("battery") is not None:
            info.update(got, channel=ch)
            return info
        if ch == channel:
            log("cached channel did not answer")
    if sys.platform == "win32" and name:
        bat = _windows_battery(name)
        if bat is not None:
            info["battery"] = bat
            info["partial"] = True
            return info
    if not tried_any:
        raise ShokzError(msg("err.shokz_unreachable",
                             "Couldn't connect to the earphones over Bluetooth ({error}). Turn them on, take them "
                             "out of the charger and keep them near the computer.", error=str(last_error or "?")))
    raise ShokzError(msg("err.shokz_no_answer", "The earphones are connected but didn't answer. "
                         "Turn Bluetooth off and on again on the earphones, then retry."))


def play_hours(name: str, source: str | None) -> float:
    n = (name or "").lower()
    model = "openswim pro" if "openswim pro" in n else "openswim" if "openswim" in n else None
    if not model:
        return 8.0
    return PLAY_HOURS[model]["bluetooth" if source == "bluetooth" else "mp3"]
