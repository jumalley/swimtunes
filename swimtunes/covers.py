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


def square_jpeg(raw: bytes, ffmpeg: str, size: int = 300, workdir: Path | None = None) -> bytes:
    """Any picture (jpg/png/webp/gif…) cropped to a square JPEG of size x size."""
    own = workdir is None
    workdir = Path(workdir or tempfile.mkdtemp(prefix="swimtunes-pic-"))
    try:
        src, out = workdir / "pic-src", workdir / "pic.jpg"
        src.write_bytes(raw)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(src), "-frames:v", "1", "-vf",
                        f"crop='min(iw,ih)':'min(iw,ih)',scale={size}:{size}", "-q:v", "3", str(out)],
                       check=True, capture_output=True, creationflags=flags)
        return out.read_bytes()
    finally:
        if own:
            import shutil
            shutil.rmtree(workdir, ignore_errors=True)


def download(url: str, limit: int = 8_000_000) -> bytes:
    if not url.startswith(("https://", "http://")):
        raise ValueError("not a web address")
    req = urllib.request.Request(url, headers={"User-Agent": "SwimTunes"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(limit)


GENERIC = {"downloads", "music", "songs", "playlist", "mix", "new folder", "misc", "other", "root"}


def suggest(tracks: list, folder: str, query: str | None = None, fetch=None) -> dict:
    """Pictures that fit a folder, from its songs and from Deezer:
    {"songs": [song paths with a picture], "web": [{"url", "kind", "label"}], "keywords": [...]}."""
    import os
    from collections import Counter
    from urllib.parse import quote

    from .meta import _DEEZER, _get

    fetch = fetch or (lambda url: _get(url, _DEEZER, tries=2))
    songs, seen_albums = [], set()
    web: list[dict] = []
    for t in tracks:      # one picture per album, so the choice isn't 20 times the same cover
        key = (t.meta or {}).get("cover") or f"{t.artist}|{t.path.name}"
        if t.has_cover and key not in seen_albums:
            seen_albums.add(key)
            songs.append(t)
    artists = Counter(a.strip() for t in tracks for a in (t.artist or "").split(",")[:1] if a.strip())
    genres = Counter((t.meta or {}).get("genre") for t in tracks if (t.meta or {}).get("genre"))
    styles = Counter(tag for t in tracks for tag in ((t.meta or {}).get("tags") or [])[:2])
    keywords = []
    if query:
        keywords.append(query.strip())
    elif folder.lower() not in GENERIC:
        keywords.append(folder)
    keywords += [g for g, _ in genres.most_common(2)] + [s for s, _ in styles.most_common(2)]
    keywords = list({k.lower(): k for k in reversed([k for k in keywords if k])}.values())[::-1][:5]
    top_artists = [a for a, _ in artists.most_common(6 if not query else 0)]

    if not os.environ.get("SWIMTUNES_OFFLINE"):
        def add(url, kind, label):
            if url and url not in {w["url"] for w in web}:
                web.append({"url": url, "kind": kind, "label": label})
        for i, kw in enumerate(keywords):
            n = 8 if i == 0 else 4
            for kind, path, field in (("album", "album", "cover_big"), ("playlist", "playlist", "picture_big")):
                try:
                    data = fetch(f"https://api.deezer.com/search/{path}?q={quote(kw)}&limit={n}") or {}
                except Exception:
                    continue
                for it in data.get("data", [])[:n]:
                    add(it.get(field) or it.get(field.replace("_big", "_xl")), kind, it.get("title") or kw)
        for a in top_artists:
            try:
                data = fetch(f"https://api.deezer.com/search/artist?q={quote(a)}&limit=1") or {}
            except Exception:
                continue
            for it in data.get("data", [])[:1]:
                pic = it.get("picture_big") or it.get("picture_xl")
                if pic and "/artist//" not in pic:      # Deezer's blank placeholder
                    add(pic, "artist", it.get("name") or a)
    return {"songs": [t.path for t in songs[:24]], "web": web[:40], "keywords": keywords + top_artists}


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
