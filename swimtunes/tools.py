"""Locate the helper programs SwimTunes needs (ffmpeg and deno).

In the double-click app they are bundled inside the app. When installed with
pip they come from the `imageio-ffmpeg` and `deno` packages. A copy installed
on the system (on PATH) also works.
"""

from __future__ import annotations

import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path

EXE = ".exe" if os.name == "nt" else ""


def bundle_dir() -> Path | None:
    """Folder with bundled files when running as the packaged app."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else None


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


@lru_cache(maxsize=None)
def ffmpeg_path() -> str | None:
    b = bundle_dir()
    if b and (b / "bin" / f"ffmpeg{EXE}").is_file():
        return str(b / "bin" / f"ffmpeg{EXE}")
    try:
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
        if p and os.path.isfile(p):
            return p
    except Exception:
        pass
    return shutil.which("ffmpeg")


@lru_cache(maxsize=None)
def deno_path() -> str | None:
    b = bundle_dir()
    if b and (b / "bin" / f"deno{EXE}").is_file():
        return str(b / "bin" / f"deno{EXE}")
    try:
        from deno import find_deno_bin
        return find_deno_bin()
    except Exception:
        pass
    return shutil.which("deno")


def health() -> list[dict]:
    """What SwimTunes needs, and whether it's there. Shown in Settings."""
    import subprocess
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    rows = []
    try:
        import yt_dlp
        rows.append({"name": "YouTube downloader (yt-dlp)", "ok": True, "detail": yt_dlp.version.__version__})
    except Exception as e:
        rows.append({"name": "YouTube downloader (yt-dlp)", "ok": False, "detail": str(e)})
    for _name, label, path, args in (("ffmpeg", "MP3 converter (ffmpeg)", ffmpeg_path(), ["-version"]),
                                    ("deno", "YouTube helper (deno)", deno_path(), ["--version"])):
        detail, ok = "missing", False
        if path:
            try:
                out = subprocess.run([path, *args], capture_output=True, text=True, timeout=60,
                                     creationflags=flags).stdout
                first = out.splitlines()[0] if out else ""
                ok = bool(first)
                detail = (first.split(" Copyright")[0] if first else "doesn't start")
            except Exception as e:
                detail = f"doesn't start: {e}"
        rows.append({"name": label, "ok": ok, "detail": detail, "path": path})
    try:
        import yt_dlp_ejs  # noqa: F401
        rows.append({"name": "YouTube challenge solver (yt-dlp-ejs)", "ok": True, "detail": "installed"})
    except ImportError:
        rows.append({"name": "YouTube challenge solver (yt-dlp-ejs)", "ok": False, "detail": "missing"})
    return rows
