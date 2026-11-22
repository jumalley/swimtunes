"""Copy the library onto the earphones (they show up as a USB drive).

Shokz OpenSwim plays songs in the order the files were *copied* onto it, not
alphabetically. So when a folder changes, it is wiped on the device and
re-copied in playlist order. macOS junk files (._Song.mp3, .DS_Store), which
the earphones would try to play, are removed.
"""

from __future__ import annotations

import os
import shutil
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
