"""Download songs from YouTube (or Spotify links, matched on YouTube) as MP3s,
skipping anything that is already in the library."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import i18n, spotify, tools
from .i18n import msg
from .library import Library, unique_path, write_tags
from .meta import estimate_bpm, tempo_label
from .text import (channel_artist, clean_title, looks_like_id, norm_title, safe_name, same_song, song_key,
                   split_artist_title)

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


def youtube_song(info: dict) -> dict:
    """What we know about a YouTube video: artist, title, whether the artist can be trusted,
    the swapped reading to try ("Song - Artist"), and other words that describe it."""
    vid = info.get("id") or ""
    raw = info.get("title") or ""
    channel = info.get("channel") or info.get("uploader") or ""
    extra = " ".join([channel, *(info.get("tags") or [])[:25], *(info.get("categories") or []), info.get("album") or ""])
    out = {"title": "", "artist": "", "reliable": False, "alt": None, "text": extra.strip(),
           "duration": info.get("duration") or 0, "unknown": looks_like_id(raw, vid)}
    track = info.get("track")
    artist = info.get("artist") or info.get("creator") or (", ".join(info["artists"]) if info.get("artists") else "")
    if track and artist:  # YouTube Music knows the song
        out.update(title=clean_title(track), artist=artist, reliable=True)
    elif out["unknown"]:
        out.update(title=raw or vid, artist=channel_artist(channel))
    else:
        a, t = split_artist_title(clean_title(raw) or raw)
        if a:
            out.update(artist=a, title=clean_title(t), reliable=True, alt=[clean_title(t), a])
        else:
            topic = channel.lower().endswith(" - topic")   # YouTube's official artist channels
            out.update(artist=channel_artist(channel), title=clean_title(raw), reliable=topic)
    return out


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
                 log: Callable[[str], None] = print, labels: dict | None = None):
        cfg = library.config()
        self.lib = library
        self.bitrate = bitrate or cfg["bitrate"]
        self.normalize = cfg["normalize"] if normalize is None else normalize
        self.allow_dupes = allow_dupes
        self.scope = scope or cfg["dupe_scope"]
        self.log = log
        self.labels = i18n.labels(labels)   # names of folders SwimTunes creates, in the user's language
        self._item = None                   # song being downloaded (gets status and % for the dashboard)
        self.ffmpeg = tools.ffmpeg_path()
        if not self.ffmpeg:
            raise RuntimeError("ffmpeg is missing. Reinstall SwimTunes (see README).")

    # ----- yt-dlp wrappers (overridden in tests) -----
    def _ydl(self, **opts):
        import yt_dlp
        base = {"quiet": True, "no_warnings": True, "noprogress": True, "ffmpeg_location": self.ffmpeg}
        if deno := tools.deno_path():  # YouTube needs a JavaScript runtime
            base["js_runtimes"] = {"deno": {"path": deno}}
        return yt_dlp.YoutubeDL({**base, **opts})

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
        def hook(d):
            if self._item is not None and d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                if total:
                    self._item["pct"] = int(d.get("downloaded_bytes", 0) * 100 / total)
        with self._ydl(format="bestaudio/best", outtmpl=str(tmpdir / "%(id)s.%(ext)s"), noplaylist=True,
                       progress_hooks=[hook]) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
            for d in info.get("requested_downloads") or []:
                if d.get("filepath"):
                    return Path(d["filepath"])
            return Path(ydl.prepare_filename(info))

    # ----- conversion -----
    def to_mp3(self, src: Path, dst: Path) -> None:
        cmd = [self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vn",
               "-map_metadata", "-1", "-c:a", "libmp3lame", "-b:a", f"{self.bitrate}k", "-ar", "44100", "-f", "mp3"]
        if self.normalize:
            cmd += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no flashing console window on Windows
        subprocess.run(cmd + [str(dst)], check=True, capture_output=True, creationflags=flags)

    # ----- step 1: find the songs behind a link (nothing is downloaded) -----
    def resolve(self, url: str) -> dict:
        """{'name': playlist name, 'items': [song dicts]} for a YouTube or Spotify link."""
        url = url.strip()
        if spotify.is_spotify(url):
            data = spotify.fetch(url)
            items = [{"kind": "spotify", "artist": t["artist"], "title": t["title"], "duration": t["duration"]}
                     for t in data["tracks"]]
            name = data["name"]
        else:
            name, entries = self.list_entries(url)
            items = []
            for e in entries:
                song = youtube_song(e)
                items.append({"kind": "youtube", "video_id": e["id"], "artist": song["artist"], "title": song["title"],
                              "duration": song["duration"], "reliable": song["reliable"], "alt": song["alt"],
                              "text": song["text"], "unknown": song["unknown"],
                              # read the video's own page when the playlist says too little about it
                              "needs_details": song["unknown"] or not song["duration"] or not song["reliable"]})
        for it in items:
            it["url"] = url
        self.mark_existing(items)
        return {"name": name or "Downloads", "items": items}

    def add_details(self, it: dict) -> None:
        """Read the video's own page for a better title, length, artist and tags.
        Marks the song 'unavailable' when YouTube won't show it (private or deleted)."""
        try:
            info = self.video_info(it["video_id"])
        except Exception as e:
            msg_ = str(e).splitlines()[0] if str(e) else type(e).__name__
            it["unavailable"] = msg_.replace("ERROR: ", "")[:160]
            return
        song = youtube_song(info)
        if song["unknown"]:
            return
        it.update(title=song["title"], artist=song["artist"], reliable=song["reliable"], alt=song["alt"],
                  text=song["text"], unknown=False, duration=song["duration"] or it.get("duration") or 0)

    def mark_existing(self, items: list[dict], folder: str | None = None) -> None:
        """Flag songs already in the library, or listed twice in this batch."""
        seen: list[tuple] = []
        for it in items:
            it["existing"] = None
            if self.allow_dupes:
                continue
            src = f"youtube:{it['video_id']}" if it.get("video_id") else None
            t = self.lib.find_existing(artist=it["artist"], title=it["title"], source=src,
                                       folder=it.get("folder") or folder, scope=self.scope)
            if t:
                it["existing"] = f"{t.folder}/{t.filename}"
                continue
            key = song_key(it["artist"], it["title"])
            if any((src and src == s) or same_song(key, k) for s, k in seen):
                it["existing"] = "listed twice"
                continue
            seen.append((src, key))

    # ----- step 2: download chosen songs into their folders -----
    def download_items(self, items: list[dict], *, after_each: Callable[[dict], None] | None = None,
                       should_stop: Callable[[], bool] | None = None) -> Result:
        """Each item needs 'folder'. Items with 'tempo_folder' get their folder chosen after the
        download, once the tempo has been measured from the audio. Each item gets a 'status'
        (waiting, searching, downloading, converting, measuring, done, skipped, failed, stopped)."""
        res = Result(folder=", ".join(sorted({it.get("folder", "") for it in items})))
        for it in items:
            it["status"] = "waiting"
        for i, it in enumerate(items, 1):
            if should_stop and should_stop():
                for rest in items[i - 1:]:
                    rest["status"] = "stopped"
                self.log(msg("log.dl_stopped", "Stopped. {n} song(s) were not downloaded.", n=len(items) - i + 1))
                break
            label = f"{it['artist']} - {it['title']}" if it.get("artist") else it["title"]
            prefix = f"[{i}/{len(items)}]"
            self._item = it
            try:
                self._one(it, res, prefix, i, len(items))
            except Exception as e:  # keep going with the rest
                err = str(e).splitlines()[0][:200] if str(e) else type(e).__name__
                res.failed.append((label, err))
                it["status"], it["error"] = "failed", err
                self.log(msg("log.dl_failed", "[{i}/{n}] FAILED {song}: {error}", i=i, n=len(items), song=label, error=err))
            finally:
                self._item = None
            if after_each:
                after_each(it)
        self.log(msg("log.dl_done", "Done: {done} downloaded, {skipped} skipped (already have), {failed} failed.",
                     done=len(res.downloaded), skipped=len(res.skipped), failed=len(res.failed)))
        return res

    def download(self, url: str, folder: str | None = None) -> Result:
        """Download everything behind a link into one folder (used by the command line)."""
        data = self.resolve(url)
        folder = safe_name(folder or data["name"])
        self.log(f"{len(data['items'])} song(s) -> folder '{folder}'")
        for it in data["items"]:
            it["folder"] = folder
        res = self.download_items(data["items"])
        res.folder = folder
        return res

    def _existing(self, artist, title, source, folder):
        if self.allow_dupes:
            return None
        return self.lib.find_existing(artist=artist, title=title, source=source, folder=folder, scope=self.scope)

    def _one(self, it: dict, res: Result, prefix: str, i: int = 1, n: int = 1) -> None:
        artist, title = it["artist"], it["title"]
        folder = safe_name(it.get("folder") or self.labels["downloads"])
        label = f"{artist} - {title}" if artist else title
        video_id = it.get("video_id")
        source = f"youtube:{video_id}" if video_id else None
        if (t := self._existing(artist, title, source, folder)):
            return self._skip(res, it, i, n, label, t)
        if not video_id:  # Spotify song: find it on YouTube
            it["status"] = "searching"
            results = self.search(f"{artist} - {title}")
            if not results:
                raise RuntimeError("no match found on YouTube")
            best = min(results, key=lambda e: score_search_result(e, artist, title, it.get("duration") or 0))
            video_id = it["video_id"] = best["id"]
            source = f"youtube:{video_id}"
            if (t := self._existing("", "", source, folder)):
                return self._skip(res, it, i, n, label, t)

        it["status"], it["pct"] = "downloading", 0
        self.log(msg("log.dl_downloading", "[{i}/{n}] Downloading {song}", i=i, n=n, song=label))
        meta = dict(it.get("meta") or {}) or None
        with tempfile.TemporaryDirectory(prefix="swimtunes-") as tmp:
            src = self.fetch_audio(video_id, Path(tmp))
            part = Path(tmp) / "song.mp3"
            it["status"] = "converting"
            try:
                self.to_mp3(src, part)
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"ffmpeg failed: {e.stderr.decode(errors='replace').strip()[:200]}") from e
            if meta is not None and not meta.get("bpm"):
                it["status"] = "measuring"
                try:
                    if bpm := estimate_bpm(part, self.ffmpeg):
                        meta["bpm"], meta["bpm_measured"] = bpm, True
                except Exception:
                    pass
            if it.get("tempo_folder"):
                folder = safe_name(tempo_label((meta or {}).get("bpm"), self.labels))
            self.lib.create_folder(folder)
            write_tags(part, title=title, artist=artist, album=folder, source=source,
                       track_no=self.lib.next_track_no(folder), meta=meta)
            out = unique_path(self.lib.folder_path(folder) / f"{safe_name(label)}.mp3")
            shutil.move(str(part), str(out))
        it["saved_to"] = f"{folder}/{out.name}"
        it["status"], it["meta"] = "done", meta
        res.downloaded.append(label)

    def _skip(self, res: Result, it: dict, i: int, n: int, label: str, existing) -> None:
        where = f"{existing.folder}/{existing.filename}"
        res.skipped.append((label, f"already in {where}"))
        it["status"], it["where"] = "skipped", where
        self.log(msg("log.dl_skip", "[{i}/{n}] Skip {song} (already in {where})", i=i, n=n, song=label, where=where))
