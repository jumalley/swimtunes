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
