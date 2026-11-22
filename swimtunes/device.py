"""Copy the library onto the earphones (they show up as a USB drive).

Shokz OpenSwim plays songs in the order the files were *copied* onto it, not
alphabetically. So when a folder changes, it is wiped on the device and
re-copied in playlist order. macOS junk files (._Song.mp3, .DS_Store), which
the earphones would try to play, are removed.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Callable

from .library import Library

JUNK = (".DS_Store", ".Spotlight-V100", ".Trashes", ".fseventsd")


def _junk_files(root: Path):
    for p in root.rglob("*"):
        if p.name.startswith("._") or p.name in JUNK:
            yield p


def sync(lib: Library, device: Path | str, *, folders: list[str] | None = None, mirror: bool = False,
         dry_run: bool = False, log: Callable[[str], None] = print) -> dict:
    device = Path(device).expanduser()
    if not device.is_dir():
        raise FileNotFoundError(f"Device not found at {device}. Are the earphones plugged in?")
    names = folders or lib.folders()
    plan_copy, plan_delete = [], []

    for name in names:
        tracks = lib.tracks(name)
        dest = device / name
        want = [t.filename for t in tracks]
        have = [f for f in os.listdir(dest) if f.lower().endswith(".mp3") and not f.startswith(".")] if dest.is_dir() else []
        if have == want:  # os.listdir on FAT returns directory (= play) order
            continue
        plan_copy.append((name, tracks, dest, have))

    if mirror:
        lib_names = set(lib.folders())
        for d in device.iterdir():
            if d.is_dir() and not d.name.startswith(".") and d.name not in lib_names:
                files = [f for f in d.iterdir() if not f.name.startswith(".")]
                if files and all(f.suffix.lower() == ".mp3" for f in files):
                    plan_delete.append(d)

    need = sum(t.size for _, tracks, _, _ in plan_copy for t in tracks)
    free = shutil.disk_usage(device).free
    freed = sum(f.stat().st_size for _, _, dest, have in plan_copy for f in (dest / h for h in have))
    freed += sum(f.stat().st_size for d in plan_delete for f in d.iterdir())
    if need > free + freed:
        raise RuntimeError(f"Not enough space on the earphones: need {need/1e6:.0f} MB, "
                           f"have {(free+freed)/1e6:.0f} MB. Remove some folders and try again.")

    junk = list(_junk_files(device))
    summary = {"updated": [n for n, *_ in plan_copy], "removed": [d.name for d in plan_delete],
               "junk_removed": len(junk), "unchanged": [n for n in names if n not in {p[0] for p in plan_copy}]}
    if dry_run:
        for n, tracks, _, _ in plan_copy:
            log(f"would copy  {n} ({len(tracks)} songs)")
        for d in plan_delete:
            log(f"would remove {d.name}")
        return summary

    for p in junk:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
    for d in plan_delete:
        log(f"Removing {d.name} from device")
        shutil.rmtree(d)
    for name, tracks, dest, have in plan_copy:
        log(f"Copying {name} ({len(tracks)} songs)")
        dest.mkdir(exist_ok=True)
        for h in have:
            (dest / h).unlink()
        for t in tracks:  # one by one, in order, so the earphones play them in order
            shutil.copyfile(t.path, dest / t.filename)
    if hasattr(os, "sync"):
        os.sync()
    log("Sync finished. Eject the earphones before unplugging.")
    return summary


# ---------- finding the earphones ----------
_SWIM_HINTS = ("swim", "shokz", "openswim", "aftershokz")


def _usage(path: str) -> tuple[int, int]:
    try:
        u = shutil.disk_usage(path)
        return u.total, u.free
    except OSError:
        return 0, 0


def _windows_drives() -> list[dict]:
    import ctypes
    k32 = ctypes.windll.kernel32
    mask = k32.GetLogicalDrives()
    out = []
    old_mode = k32.SetErrorMode(1)  # no "insert a disk" pop-ups for empty card readers
    try:
        out = _windows_scan(k32, mask)
    finally:
        k32.SetErrorMode(old_mode)
    return out


def _windows_scan(k32, mask) -> list[dict]:
    import ctypes
    import string
    out = []
    for i, letter in enumerate(string.ascii_uppercase):
        if not (mask >> i) & 1:
            continue
        root = f"{letter}:\\"
        if k32.GetDriveTypeW(ctypes.c_wchar_p(root)) != 2:  # 2 = removable (USB)
            continue
        buf = ctypes.create_unicode_buffer(261)
        ok = k32.GetVolumeInformationW(ctypes.c_wchar_p(root), buf, 261, None, None, None, None, 0)
        if not ok:  # e.g. empty card reader slot
            continue
        out.append({"path": root, "label": buf.value or "USB drive"})
    return out


def _mac_drives() -> list[dict]:
    out = []
    vol = Path("/Volumes")
    if vol.is_dir():
        for d in vol.iterdir():
            try:
                if os.path.realpath(d) == "/" or not os.path.ismount(d):
                    continue
            except OSError:
                continue
            out.append({"path": str(d), "label": d.name})
    return out


def _linux_drives() -> list[dict]:
    user = os.environ.get("USER") or ""
    out = []
    for base in (Path("/media") / user, Path("/run/media") / user, Path("/media")):
        if base.is_dir():
            for d in base.iterdir():
                if d.is_dir() and os.path.ismount(d) and not any(x["path"] == str(d) for x in out):
                    out.append({"path": str(d), "label": d.name})
    return out


def find_drives() -> list[dict]:
    """USB drives that could be the earphones, most likely first."""
    try:
        if os.name == "nt":
            drives = _windows_drives()
        elif sys.platform == "darwin":
            drives = _mac_drives()
        else:
            drives = _linux_drives()
    except Exception:
        drives = []
    for d in drives:
        d["total"], d["free"] = _usage(d["path"])
        d["likely"] = any(h in d["label"].lower() for h in _SWIM_HINTS)
    drives = [d for d in drives if d["total"]]
    drives.sort(key=lambda d: (not d["likely"], d["total"] > 64e9, d["label"].lower()))
    return drives
