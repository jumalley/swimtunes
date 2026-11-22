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

from .i18n import msg
from .library import Library

JUNK = (".DS_Store", ".Spotlight-V100", ".Trashes", ".fseventsd")
AUDIO = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma", ".m4b"}


def _audio_files(folder: Path) -> list[str]:
    """Audio files in a device folder, in play order."""
    if not folder.is_dir():
        return []
    return [f for f in _play_order(folder) if Path(f).suffix.lower() in AUDIO and not f.startswith(".")]


def _play_order(folder: Path) -> list[str]:
    """Files in the order the earphones play them. On the earphones' FAT32
    drive, os.listdir returns them in the order they were copied."""
    return os.listdir(folder)


def _junk_files(root: Path):
    for p in root.rglob("*"):
        if p.name.startswith("._") or p.name in JUNK:
            yield p


def sync(lib: Library, device: Path | str, *, folders: list[str] | None = None, mirror: bool = False,
         dry_run: bool = False, replace: bool = False, log: Callable[[str], None] = print,
         progress: Callable[[int, int], None] | None = None) -> dict:
    """Make the earphones match SwimTunes. A folder holding songs that exist only on the
    earphones is left alone (import it first) unless replace=True."""
    device = Path(device).expanduser()
    if not device.is_dir():
        raise FileNotFoundError(msg("err.no_earphones", "Earphones not found at {path}. Are they plugged in?", path=device))
    names = folders or lib.folders()
    plan_copy, plan_delete, protected = [], [], []

    for name in names:
        tracks = lib.tracks(name)
        dest = device / name
        want = [t.filename for t in tracks]
        have = _audio_files(dest)
        if have == want:
            continue
        extra = unknown_songs(lib, dest, have, want) if have else []
        if extra and not replace:
            protected.append({"folder": name, "only_on_earphones": len(extra)})
            log(msg("log.sync_protected", "Skipped {folder}: {n} song(s) there are only on the earphones. Import them first.",
                    folder=name, n=len(extra)))
            continue
        plan_copy.append((name, tracks, dest, have))

    if mirror:
        lib_names = set(lib.folders())
        for d in device.iterdir():
            if d.is_dir() and not d.name.startswith(".") and d.name not in lib_names:
                files = [f for f in d.iterdir() if not f.name.startswith(".")]
                if files and all(f.suffix.lower() in AUDIO for f in files):
                    plan_delete.append(d)

    need = sum(t.size for _, tracks, _, _ in plan_copy for t in tracks)
    free = shutil.disk_usage(device).free
    freed = sum(f.stat().st_size for _, _, dest, have in plan_copy for f in (dest / h for h in have))
    freed += sum(f.stat().st_size for d in plan_delete for f in d.iterdir())
    if need > free + freed:
        raise RuntimeError(msg("err.no_space", "Not enough space on the earphones: need {need} MB, have {have} MB. "
                               "Remove some folders and try again.", need=round(need / 1e6), have=round((free + freed) / 1e6)))

    junk = list(_junk_files(device))
    summary = {"updated": [n for n, *_ in plan_copy], "removed": [d.name for d in plan_delete], "protected": protected,
               "junk_removed": len(junk), "unchanged": [n for n in names if n not in {p[0] for p in plan_copy} and n not in {p["folder"] for p in protected}]}
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
        log(msg("log.sync_removing", "Removing {folder} from the earphones", folder=d.name))
        shutil.rmtree(d)
    total, done = sum(len(tr) for _, tr, _, _ in plan_copy), 0
    for name, tracks, dest, have in plan_copy:
        log(msg("log.sync_copying", "Copying {folder} ({n} songs)", folder=name, n=len(tracks)))
        dest.mkdir(exist_ok=True)
        for h in have:
            (dest / h).unlink()
        for t in tracks:  # one by one, in order, so the earphones play them in order
            shutil.copyfile(t.path, dest / t.filename)
            done += 1
            if progress:
                progress(done, total)
    if hasattr(os, "sync"):
        os.sync()
    if not plan_copy and not plan_delete:
        log(msg("log.sync_uptodate", "The earphones are already up to date."))
    log(msg("log.sync_done", "Sync finished. Eject the earphones before unplugging."))
    return summary


# ---------- what's on the earphones ----------
STANDARD_SIZES = (4, 8, 16, 32, 64, 128)


def capacity_gb(total_bytes: float) -> int:
    """The size printed on the box (a '32 GB' drive reports a little less)."""
    for size in STANDARD_SIZES:
        if total_bytes <= size * 1e9 * 1.02:
            return size
    return int(round(total_bytes / 1e9))


def _song_of(path: Path) -> tuple[str, str]:
    from .library import read_track
    from .text import split_artist_title
    if path.suffix.lower() == ".mp3":
        t = read_track(path, path.parent.name)
        return t.artist, t.title
    return split_artist_title(path.stem)


def unknown_songs(lib: Library, folder: Path, have: list[str], want: list[str]) -> list[str]:
    """Files on the earphones whose song SwimTunes doesn't have anywhere."""
    want_stems = {Path(w).stem.lower() for w in want}
    out = []
    for h in have:
        if Path(h).stem.lower() in want_stems:
            continue
        artist, title = _song_of(folder / h)
        if not lib.find_existing(artist=artist, title=title, scope="library"):
            out.append(h)
    return out


def compare(lib: Library, device: Path | str) -> dict:
    """Folder by folder: what's on the earphones vs in SwimTunes."""
    device = Path(device)
    if not device.is_dir():
        raise FileNotFoundError(msg("err.no_earphones", "Earphones not found at {path}. Are they plugged in?", path=device))
    lib_names = lib.folders()
    dev_names = sorted((d.name for d in device.iterdir() if d.is_dir() and not d.name.startswith(".")
                        and d.name not in ("System Volume Information", "$RECYCLE.BIN")), key=str.lower)
    rows = []
    for name in sorted(set(lib_names) | set(dev_names), key=str.lower):
        on_dev = _audio_files(device / name) if name in dev_names else []
        if name in dev_names and not on_dev and name not in lib_names:
            continue  # not a music folder
        in_lib = [t.filename for t in lib.tracks(name)] if name in lib_names else []
        dev_size = sum((device / name / f).stat().st_size for f in on_dev)
        if name not in lib_names:
            status = "only_on_earphones"
        elif name not in dev_names or not on_dev:
            status = "only_in_swimtunes"
        elif on_dev == in_lib:
            status = "same"
        else:
            status = "different"
        rows.append({"name": name, "status": status, "on_earphones": len(on_dev), "in_swimtunes": len(in_lib),
                     "only_on_earphones": len(unknown_songs(lib, device / name, on_dev, in_lib)),
                     "missing_on_earphones": len([f for f in in_lib if f not in on_dev]),
                     "earphones_size": dev_size})
    loose = [f.name for f in device.iterdir() if f.is_file() and f.suffix.lower() in AUDIO and not f.name.startswith(".")]
    usage = shutil.disk_usage(device)
    return {"path": str(device), "folders": rows, "loose_files": len(loose),
            "total": usage.total, "free": usage.free, "capacity_gb": capacity_gb(usage.total)}


def import_from_device(lib: Library, device: Path | str, folders: list[str], *, ffmpeg: str | None,
                       log: Callable[[str], None] = print, scope: str | None = None,
                       progress: Callable[[int, int], None] | None = None) -> dict:
    """Copy songs from the earphones into SwimTunes, keeping folders and play order,
    converting other formats to MP3 and skipping songs SwimTunes already has."""
    import subprocess
    from .library import read_track, unique_path, write_tags
    from .text import safe_name, split_artist_title
    device = Path(device)
    done = {"imported": 0, "skipped": 0, "failed": 0}
    total = sum(len(_audio_files(device / n if n != "." else device)) for n in folders)
    count = 0
    for name in folders:
        src_dir = device / name if name != "." else device
        target = safe_name(name if name != "." else "From earphones")
        files = _audio_files(src_dir)
        log(msg("log.import_folder", "Importing {folder} ({n} songs)", folder=target, n=len(files)))
        lib.create_folder(target)
        for f in files:
            src = src_dir / f
            count += 1
            if progress:
                progress(count, total)
            try:
                if src.suffix.lower() == ".mp3":
                    t = read_track(src, target)
                    artist, title = t.artist, t.title
                else:
                    artist, title = split_artist_title(src.stem)
                existing = lib.find_existing(artist=artist, title=title, folder=target, scope=scope)
                if existing:
                    if existing.folder == target and existing.filename == f:
                        log(msg("log.import_there", "  already there: {file}", file=f))
                    else:
                        log(msg("log.import_skip", "  skip {file} (already in {where})", file=f,
                                where=f"{existing.folder}/{existing.filename}"))
                    done["skipped"] += 1
                    continue
                out = unique_path(lib.folder_path(target) / (Path(f).stem + ".mp3"))
                if src.suffix.lower() == ".mp3":
                    shutil.copyfile(src, out)
                else:
                    if not ffmpeg:
                        raise RuntimeError("ffmpeg missing")
                    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(src), "-vn", "-c:a", "libmp3lame",
                                    "-b:a", f"{lib.config()['bitrate']}k", "-f", "mp3", str(out)],
                                   check=True, capture_output=True, creationflags=flags)
                write_tags(out, title=title or out.stem, artist=artist, album=target,
                           track_no=lib.next_track_no(target))
                done["imported"] += 1
            except Exception as e:
                done["failed"] += 1
                log(msg("log.import_failed", "  FAILED {file}: {error}", file=f, error=e))
    log(msg("log.import_done", "Import finished: {imported} imported, {skipped} already in SwimTunes, {failed} failed.",
            **done))
    return done


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
