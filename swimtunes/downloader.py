"""Download songs from YouTube (or Spotify links, matched on YouTube) as MP3s,
skipping anything that is already in the library."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import i18n, spotify, tools
from .i18n import msg
from .library import Library, unique_path, write_cover, write_tags
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


BROWSER_NAMES = {"chrome": "Chrome", "edge": "Edge", "firefox": "Firefox", "brave": "Brave", "opera": "Opera",
                 "vivaldi": "Vivaldi", "chromium": "Chromium", "safari": "Safari", "whale": "Whale"}


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
        self._local = threading.local()     # per download thread: the song it works on (gets status and %)
        self.workers = 3                    # songs downloaded at the same time
        self._login_ok = None               # None = not checked yet; False = browser login unreadable
        self._login_lock = threading.Lock()
        self.ffmpeg = tools.ffmpeg_path()
        if not self.ffmpeg:
            raise RuntimeError("ffmpeg is missing. Reinstall SwimTunes (see README).")

    @property
    def _item(self):
        return getattr(self._local, "item", None)

    @_item.setter
    def _item(self, value):
        self._local.item = value

    # ----- yt-dlp wrappers (overridden in tests) -----
    def _ydl(self, **opts):
        import yt_dlp
        base = {"quiet": True, "no_warnings": True, "noprogress": True, "ffmpeg_location": self.ffmpeg,
                "no_color": True, "retries": 3, "fragment_retries": 3, "extractor_retries": 2,
                "http_chunk_size": 10 * 1024 * 1024}
        if deno := tools.deno_path():  # YouTube needs a JavaScript runtime
            base["js_runtimes"] = {"deno": {"path": deno}}
        base.update(self._login_options())   # the user's YouTube login, if connected and readable
        return yt_dlp.YoutubeDL({**base, **opts})

    def _login_options(self) -> dict:
        """The YouTube login, checked once. If the browser's login can't be read (Chrome open, or
        Chrome/Edge on Windows refusing), carry on without it instead of failing every song."""
        from . import accounts
        opts = accounts.ytdlp_options()
        if "cookiesfrombrowser" not in opts:
            return opts
        with self._login_lock:
            if self._login_ok is None:
                import yt_dlp
                try:
                    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "no_color": True, **opts}) as y:
                        _ = y.cookiejar                 # reads the browser's login now
                    self._login_ok = True
                except Exception as e:
                    self._login_ok = False
                    kind = accounts.explain_youtube_error(str(e))
                    kind = kind if kind in ("browser_open", "browser_locked", "browser_missing") else "other"
                    texts = {
                        "browser_open": "{browser} is open, so SwimTunes can't read your YouTube login.",
                        "browser_locked": "{browser} doesn't share your YouTube login with other apps on this computer.",
                        "browser_missing": "{browser} isn't installed here, so there's no YouTube login to use.",
                        "other": "Couldn't read your YouTube login from {browser}.",
                    }
                    self.log(msg("log.yt_login_skipped_" + kind, texts[kind] + " Downloading without it: public songs "
                                 "work, private ones won't. To fix it, use Firefox or a cookies.txt file in Settings → Accounts.",
                                 browser=BROWSER_NAMES.get(opts["cookiesfrombrowser"][0], opts["cookiesfrombrowser"][0])))
        return opts if self._login_ok else {}

    def my_playlists(self) -> list[dict]:
        """The connected YouTube account's playlists, private ones included."""
        with self._ydl(extract_flat="in_playlist", playlistend=200) as ydl:
            info = ydl.extract_info("https://www.youtube.com/feed/playlists", download=False)
        out = [{"name": "Liked videos", "url": "https://www.youtube.com/playlist?list=LL", "source": "youtube", "liked": True},
               {"name": "Watch later", "url": "https://www.youtube.com/playlist?list=WL", "source": "youtube"}]
        for e in info.get("entries") or []:
            if not e:
                continue
            url = e.get("url") or ""
            if not url.startswith("http"):
                url = f"https://www.youtube.com/playlist?list={e.get('id')}"
            thumbs = e.get("thumbnails") or []
            out.append({"name": e.get("title") or e.get("id"), "url": url, "count": e.get("playlist_count"),
                        "image": thumbs[-1]["url"] if thumbs else "", "source": "youtube"})
        return out

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

    # YouTube sometimes refuses one "client" (HTTP 403) but serves another one
    FALLBACK_CLIENTS = [None, ["tv", "web_safari"], ["mweb", "android_vr", "web_embedded"]]

    def fetch_audio(self, video_id: str, tmpdir: Path) -> Path:
        item = self._item

        def hook(d):
            if item is not None and d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                if total:
                    item["pct"] = int(d.get("downloaded_bytes", 0) * 100 / total)
        last = None
        for clients in self.FALLBACK_CLIENTS:
            extra = {"extractor_args": {"youtube": {"player_client": clients}}} if clients else {}
            try:
                with self._ydl(format="bestaudio/best", outtmpl=str(tmpdir / "%(id)s.%(ext)s"), noplaylist=True,
                               progress_hooks=[hook], **extra) as ydl:
                    info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
                    for d in info.get("requested_downloads") or []:
                        if d.get("filepath"):
                            return Path(d["filepath"])
                    return Path(ydl.prepare_filename(info))
            except Exception as e:
                last = e
                if error_kind(str(e)) not in ("blocked", "format"):
                    raise                     # private, deleted…: another client won't help
                if item is not None:
                    item["retry"] = (item.get("retry") or 0) + 1
                for f in tmpdir.glob(f"{video_id}.*"):   # remove the partial download before trying again
                    if f.is_file():
                        f.unlink(missing_ok=True)
        raise last

    def fetch_cover(self, it: dict, workdir: Path) -> Path | None:
        """Square album cover: Deezer's when known, else the YouTube thumbnail cropped square."""
        from .covers import cover_urls, make_cover
        data = make_cover(cover_urls(it.get("meta"), it.get("video_id")), self.ffmpeg, workdir)
        if not data:
            return None
        out = workdir / "cover.jpg"
        out.write_bytes(data)
        return out

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
            items = [{"kind": "spotify", "artist": t["artist"], "title": t["title"], "duration": t["duration"],
                      "cover": t.get("cover") or data.get("cover") or ""} for t in data["tracks"]]
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
            msg_ = _ANSI.sub("", str(e).splitlines()[0] if str(e) else type(e).__name__).replace("ERROR: ", "")
            it["unavailable"] = msg_[:200]
            it["unavailable_kind"] = error_kind(msg_)   # private, removed, refused, sign-in…
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
        (waiting, searching, downloading, converting, measuring, done, skipped, failed, stopped).

        Several songs are downloaded and converted at the same time, but they are saved one by
        one in list order, so the folders keep the playlist order."""
        from concurrent.futures import ThreadPoolExecutor
        res = Result(folder=", ".join(sorted({it.get("folder", "") for it in items})))
        n = len(items)
        todo, seen = [], []
        for i, it in enumerate(items, 1):
            it["status"] = "waiting"
            label = self._label(it)
            src = f"youtube:{it['video_id']}" if it.get("video_id") else None
            folder = safe_name(it.get("folder") or self.labels["downloads"])
            t = self._existing(it["artist"], it["title"], src, folder)
            if t:
                self._skip(res, it, i, n, label, f"{t.folder}/{t.filename}")
                if after_each:
                    after_each(it)
                continue
            key = song_key(it["artist"], it["title"])
            twin = next((lbl for s_, k, lbl in seen if (src and s_ == src) or same_song(key, k)), None)
            if twin and not self.allow_dupes:   # the same song twice in this list
                self._skip(res, it, i, n, label, twin)
                if after_each:
                    after_each(it)
                continue
            seen.append((src, key, label))
            todo.append((i, it))
        stopped = [False]

        def prepare(job):
            i, it = job
            if should_stop and should_stop():
                stopped[0] = True
                return None
            self._item = it
            try:
                return self._prepare(it, i, n)
            finally:
                self._item = None

        with ThreadPoolExecutor(max_workers=max(1, self.workers)) as pool:
            futures = [(i, it, pool.submit(prepare, (i, it))) for i, it in todo]
            for i, it, fut in futures:          # save in list order
                label = self._label(it)
                try:
                    work = fut.result()
                    if work is None:
                        it["status"] = "stopped"
                        continue
                    self._save(it, work, res, i, n)
                except Exception as e:          # keep going with the rest
                    raw = str(e).splitlines()[0] if str(e) else type(e).__name__
                    err = _ANSI.sub("", raw).replace("ERROR: ", "")[:220]
                    res.failed.append((label, err))
                    it["status"], it["error"], it["error_kind"] = "failed", err, error_kind(err)
                    self.log(msg("log.dl_failed", "[{i}/{n}] FAILED {song}: {error}", i=i, n=n, song=label, error=err))
                finally:
                    if after_each:
                        after_each(it)
        if stopped[0]:
            left = sum(1 for _, it in todo if it["status"] == "stopped")
            self.log(msg("log.dl_stopped", "Stopped. {n} song(s) were not downloaded.", n=left))
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

    @staticmethod
    def _label(it: dict) -> str:
        return f"{it['artist']} - {it['title']}" if it.get("artist") else it["title"]

    def _existing(self, artist, title, source, folder):
        if self.allow_dupes:
            return None
        return self.lib.find_existing(artist=artist, title=title, source=source, folder=folder, scope=self.scope)

    def _prepare(self, it: dict, i: int, n: int) -> dict:
        """Runs in a download thread: find, download, convert, measure, get the cover."""
        artist, title = it["artist"], it["title"]
        video_id = it.get("video_id")
        if not video_id:  # Spotify song: find it on YouTube
            it["status"] = "searching"
            results = self.search(f"{artist} - {title}")
            if not results:
                raise RuntimeError("no match found on YouTube")
            best = min(results, key=lambda e: score_search_result(e, artist, title, it.get("duration") or 0))
            video_id = it["video_id"] = best["id"]
        it["status"], it["pct"] = "downloading", 0
        self.log(msg("log.dl_downloading", "[{i}/{n}] Downloading {song}", i=i, n=n, song=self._label(it)))
        meta = dict(it.get("meta") or {}) or None
        tmp = Path(tempfile.mkdtemp(prefix="swimtunes-"))
        try:
            src = self.fetch_audio(video_id, tmp)
            part = tmp / "song.mp3"
            it["status"] = "converting"
            try:
                self.to_mp3(src, part)
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"ffmpeg failed: {e.stderr.decode(errors='replace').strip()[:200]}") from e
            src.unlink(missing_ok=True)
            if meta is not None and not meta.get("bpm"):
                it["status"] = "measuring"
                try:
                    if bpm := estimate_bpm(part, self.ffmpeg):
                        meta["bpm"], meta["bpm_measured"] = bpm, True
                except Exception:
                    pass
            cover = self.fetch_cover(it, tmp)
            it["status"] = "saving"
            return {"tmp": tmp, "part": part, "meta": meta, "cover": cover, "video_id": video_id}
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise

    def _save(self, it: dict, work: dict, res: Result, i: int, n: int) -> None:
        """In list order: last duplicate check, then tag and move the MP3 into its folder."""
        try:
            artist, title, meta = it["artist"], it["title"], work["meta"]
            source = f"youtube:{work['video_id']}"
            folder = safe_name(it.get("folder") or self.labels["downloads"])
            if it.get("tempo_folder"):
                folder = safe_name(tempo_label((meta or {}).get("bpm"), self.labels))
            label = self._label(it)
            if (t := self._existing(artist, title, source, folder)):
                return self._skip(res, it, i, n, label, f"{t.folder}/{t.filename}")
            self.lib.create_folder(folder)
            write_tags(work["part"], title=title, artist=artist, album=folder, source=source,
                       track_no=self.lib.next_track_no(folder), meta=meta)
            if work.get("cover"):
                write_cover(work["part"], work["cover"].read_bytes())
            out = unique_path(self.lib.folder_path(folder) / f"{safe_name(label)}.mp3")
            shutil.move(str(work["part"]), str(out))
            it["saved_to"] = f"{folder}/{out.name}"
            it["status"], it["meta"] = "done", meta
            res.downloaded.append(label)
        finally:
            shutil.rmtree(work["tmp"], ignore_errors=True)

    def _skip(self, res: Result, it: dict, i: int, n: int, label: str, where: str) -> None:
        res.skipped.append((label, f"already in {where}"))
        it["status"], it["where"] = "skipped", where
        self.log(msg("log.dl_skip", "[{i}/{n}] Skip {song} (already in {where})", i=i, n=n, song=label, where=where))


_ANSI = re.compile(r"\x1b\[[0-9;]*m|\[[0-9;]*m")


def error_kind(text: str) -> str:
    """Why a download failed, in a word the dashboard can explain."""
    t = (text or "").lower()
    if "403" in t or "forbidden" in t:
        return "blocked"
    if "confirm your age" in t or "age-restricted" in t or "age restricted" in t:
        return "age"
    if "not a bot" in t:
        return "bot"
    if "private video" in t:
        return "private"
    if "available in your country" in t or "in your country" in t and "blocked" in t or "geo-restrict" in t:
        return "region"
    if "requested format is not available" in t:
        return "format"
    if "video unavailable" in t or "has been removed" in t or "no longer available" in t:
        return "unavailable"
    if "no match found" in t:
        return "not_found"
    if "timed out" in t or "connection" in t or "network" in t or "getaddrinfo" in t:
        return "network"
    return "other"
