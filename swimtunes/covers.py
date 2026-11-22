"""Album pictures: Deezer's cover when known, else the YouTube thumbnail cropped square."""

from __future__ import annotations

import subprocess
import tempfile
import urllib.request
from pathlib import Path


def cover_urls(meta: dict | None, video_id: str | None) -> list[str]:
    urls = [meta["cover"]] if meta and meta.get("cover") else []
    if video_id:
        urls.append(f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg")
    return urls


def make_cover(urls: list[str], ffmpeg: str, workdir: Path | None = None) -> bytes | None:
    """Download the first picture that works and make it a 300x300 JPEG."""
    own = workdir is None
    workdir = Path(workdir or tempfile.mkdtemp(prefix="swimtunes-cover-"))
    try:
        for url in urls:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "SwimTunes"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    raw = r.read(3_000_000)
                src, out = workdir / "cover-src.jpg", workdir / "cover.jpg"
                src.write_bytes(raw)
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(src), "-vf",
                                "crop='min(iw,ih)':'min(iw,ih)',scale=300:300", "-q:v", "4", str(out)],
                               check=True, capture_output=True, creationflags=flags)
                return out.read_bytes()
            except Exception:
                continue
        return None
    finally:
        if own:
            import shutil
            shutil.rmtree(workdir, ignore_errors=True)
