"""Download songs from YouTube (or Spotify links, matched on YouTube) as MP3s,
skipping anything that is already in the library."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import spotify
from .library import Library, unique_path, write_tags
from .text import channel_artist, clean_title, norm_title, safe_name, split_artist_title

# Words that mean "a different version" — avoided when matching Spotify songs on YouTube
_VERSION_WORDS = ("live", "cover", "karaoke", "remix", "sped up", "slowed", "nightcore", "8d",
                  "instrumental", "reverb", "acoustic", "piano version", "1 hour", "loop", "reaction")


@dataclass
class Result:
    downloaded: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)   # (song, reason)
    failed: list[tuple[str, str]] = field(default_factory=list)    # (song, error)
    folder: str = ""

    def to_dict(self) -> dict:
        return {"folder": self.folder, "downloaded": self.downloaded,
                "skipped": [list(s) for s in self.skipped], "failed": [list(f) for f in self.failed]}


def youtube_meta(info: dict) -> tuple[str, str]:
    """Best guess of (artist, title) from YouTube metadata."""
    track = info.get("track")
    artist = info.get("artist") or info.get("creator")
    if not artist and info.get("artists"):
        artist = ", ".join(info["artists"])
    if track and artist:
        return artist, clean_title(track)
    raw = info.get("title") or info.get("id") or "Unknown"
    a, t = split_artist_title(raw)
    if a:
        return a, clean_title(t)
    return channel_artist(info.get("channel") or info.get("uploader") or ""), clean_title(raw)


def score_search_result(entry: dict, artist: str, title: str, duration: float) -> float:
    """Lower is better."""
    yt_title = (entry.get("title") or "").lower()
    channel = (entry.get("channel") or entry.get("uploader") or "").lower()
    score = 0.0
    if duration and entry.get("duration"):
        diff = abs(entry["duration"] - duration)
        score += diff if diff < 15 else 15 + diff * 3
    if norm_title(title) not in norm_title(yt_title):
        score += 40
    if artist and artist.split(",")[0].strip().lower() not in (yt_title + " " + channel):
        score += 15
    for w in _VERSION_WORDS:
        if w in yt_title and w not in title.lower():
            score += 50
    if channel.endswith(" - topic"):
        score -= 10   # YouTube Music "Topic" channels = the official studio audio
    if "official audio" in yt_title:
        score -= 5
    return score


class Downloader:
    def __init__(self, library: Library, *, bitrate: int | None = None, normalize: bool | None = None,
                 allow_dupes: bool = False, scope: str | None = None,
                 log: Callable[[str], None] = print):
        cfg = library.config()
        self.lib = library
        self.bitrate = bitrate or cfg["bitrate"]
        self.normalize = cfg["normalize"] if normalize is None else normalize
        self.allow_dupes = allow_dupes
        self.scope = scope or cfg["dupe_scope"]
        self.log = log
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg is not installed. See README for how to install it.")

    # ----- yt-dlp wrappers (overridden in tests) -----
    def _ydl(self, **opts):
        import yt_dlp
        return yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noprogress": True, **opts})

    def list_entries(self, url: str) -> tuple[str, list[dict]]:
        """(playlist title, [{'id','title','channel','duration'}]) for a video or playlist URL."""
        with self._ydl(extract_flat="in_playlist") as ydl:
            info = ydl.extract_info(url, download=False)
        if info.get("_type") == "playlist" or "entries" in info:
            entries = [e for e in (info.get("entries") or []) if e and e.get("id")]
            return info.get("title") or "YouTube playlist", entries
        return "", [info]

    def video_info(self, video_id: str) -> dict:
        with self._ydl() as ydl:
            return ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)

    def search(self, query: str, n: int = 6) -> list[dict]:
        with self._ydl(extract_flat=True) as ydl:
            info = ydl.extract_info(f"ytsearch{n}:{query}", download=False)
        return [e for e in (info.get("entries") or []) if e and e.get("id")]

    def fetch_audio(self, video_id: str, tmpdir: Path) -> Path:
        with self._ydl(format="bestaudio/best", outtmpl=str(tmpdir / "%(id)s.%(ext)s"), noplaylist=True) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
            for d in info.get("requested_downloads") or []:
                if d.get("filepath"):
                    return Path(d["filepath"])
            return Path(ydl.prepare_filename(info))

    # ----- conversion -----
    def to_mp3(self, src: Path, dst: Path) -> None:
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vn",
               "-map_metadata", "-1", "-c:a", "libmp3lame", "-b:a", f"{self.bitrate}k", "-ar", "44100", "-f", "mp3"]
        if self.normalize:
            cmd += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
        subprocess.run(cmd + [str(dst)], check=True, capture_output=True)

    # ----- main entry point -----
    def download(self, url: str, folder: str | None = None) -> Result:
        url = url.strip()
        if spotify.is_spotify(url):
            data = spotify.fetch(url)
            folder = safe_name(folder or data["name"])
            self.log(f"Spotify: '{data['name']}' — {len(data['tracks'])} songs → folder '{folder}'")
            items = [("spotify", t) for t in data["tracks"]]
        else:
            name, entries = self.list_entries(url)
            folder = safe_name(folder or name or "Downloads")
            self.log(f"YouTube: {len(entries)} video(s) → folder '{folder}'")
            items = [("youtube", e) for e in entries]

        self.lib.create_folder(folder)
        res = Result(folder=folder)
        for i, (kind, item) in enumerate(items, 1):
            label = (f"{item['artist']} - {item['title']}" if kind == "spotify" else item.get("title") or item["id"])
            prefix = f"[{i}/{len(items)}]"
            try:
                self._one(kind, item, folder, res, prefix)
            except Exception as e:  # keep going with the rest of the playlist
                msg = str(e).splitlines()[0][:200] if str(e) else type(e).__name__
                res.failed.append((label, msg))
                self.log(f"{prefix} FAILED {label}: {msg}")
        self.log(f"Done: {len(res.downloaded)} downloaded, {len(res.skipped)} skipped (already have), "
                 f"{len(res.failed)} failed.")
        return res

    def _existing(self, artist, title, source, folder):
        if self.allow_dupes:
            return None
        return self.lib.find_existing(artist=artist, title=title, source=source, folder=folder, scope=self.scope)

    def _one(self, kind: str, item: dict, folder: str, res: Result, prefix: str) -> None:
        if kind == "spotify":
            artist, title = item["artist"], item["title"]
            if (t := self._existing(artist, title, None, folder)):
                return self._skip(res, prefix, f"{artist} - {title}", t)
            results = self.search(f"{artist} - {title}")
            if not results:
                raise RuntimeError("no match found on YouTube")
            best = min(results, key=lambda e: score_search_result(e, artist, title, item["duration"]))
            video_id = best["id"]
            source = f"youtube:{video_id}"
            if (t := self._existing("", "", source, folder)):
                return self._skip(res, prefix, f"{artist} - {title}", t)
        else:
            video_id = item["id"]
            source = f"youtube:{video_id}"
            quick_artist, quick_title = youtube_meta(item)
            if (t := self._existing(quick_artist, quick_title, source, folder)):
                return self._skip(res, prefix, f"{quick_artist} - {quick_title}", t)
            info = self.video_info(video_id)   # richer metadata (official artist/track names)
            artist, title = youtube_meta(info)
            if (t := self._existing(artist, title, source, folder)):
                return self._skip(res, prefix, f"{artist} - {title}", t)

        name = f"{artist} - {title}" if artist else title
        self.log(f"{prefix} Downloading {name}")
        with tempfile.TemporaryDirectory(prefix="swimtunes-") as tmp:
            src = self.fetch_audio(video_id, Path(tmp))
            out = unique_path(self.lib.folder_path(folder) / f"{safe_name(name)}.mp3")
            part = out.with_name(f".{out.name}.part")
            try:
                self.to_mp3(src, part)
                write_tags(part, title=title, artist=artist, album=folder, source=source,
                           track_no=self.lib.next_track_no(folder))
                part.rename(out)
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"ffmpeg failed: {e.stderr.decode(errors='replace').strip()[:200]}") from e
            finally:
                part.unlink(missing_ok=True)
        res.downloaded.append(name)

    def _skip(self, res: Result, prefix: str, label: str, existing) -> None:
        where = f"{existing.folder}/{existing.filename}"
        res.skipped.append((label, f"already in {where}"))
        self.log(f"{prefix} Skip {label} (already in {where})")
