"""Find out more about a song: genre, tempo (BPM), year, style and the artist's country.

Sources (free, no account needed):
  * Deezer      - genre (Pop, Rock, Rap/Hip Hop...), BPM, release year
  * MusicBrainz - detailed styles (post-punk, synth-pop...), country of the artist

Results are cached in the library (.swimtunes-meta.json) so each song is looked up once.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import REPO, __version__
from .text import has_cyrillic, norm_artist, similar, song_key

UA = f"SwimTunes/{__version__} (https://github.com/{REPO})"
_BRACKETS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_DECADE_TAG = re.compile(r"^\d{2,4}s$")
COUNTRIES = {
    "RU": "Russia", "BY": "Belarus", "UA": "Ukraine", "KZ": "Kazakhstan", "US": "USA", "GB": "UK", "FR": "France",
    "DE": "Germany", "ES": "Spain", "IT": "Italy", "CA": "Canada", "AU": "Australia", "SE": "Sweden", "NO": "Norway",
    "NL": "Netherlands", "BE": "Belgium", "BR": "Brazil", "MX": "Mexico", "JP": "Japan", "KR": "South Korea",
    "PL": "Poland", "IE": "Ireland", "JM": "Jamaica", "NG": "Nigeria", "CO": "Colombia", "PR": "Puerto Rico",
    "FI": "Finland", "DK": "Denmark", "IS": "Iceland", "CH": "Switzerland", "AT": "Austria", "PT": "Portugal",
    "AR": "Argentina", "TR": "Turkey", "CN": "China", "IN": "India", "NZ": "New Zealand", "ZA": "South Africa",
}
EMPTY = {"genre": "", "bpm": None, "year": None, "tags": [], "country": "", "cyrillic": False}


class Throttle:
    def __init__(self, gap: float):
        self.gap, self.last, self.lock = gap, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            delay = self.last + self.gap - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.last = time.monotonic()


_DEEZER = Throttle(0.15)       # Deezer allows 50 requests / 5 s
_MUSICBRAINZ = Throttle(1.5)   # MusicBrainz allows 1 request / s; stay well under it


def _get(url: str, throttle: Throttle, tries: int = 5):
    for i in range(tries):
        throttle.wait()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and i < tries - 1:
                time.sleep(3 * (i + 1))  # busy: wait 3, 6, 9, 12 s
                continue
            raise
    return None


def country_name(code: str) -> str:
    return COUNTRIES.get(code or "", code or "")


class MetaLookup:
    def __init__(self, cache_path: Path | None = None, fetch=_get):
        self.cache_path = cache_path
        self.fetch = fetch
        self.cache = {"songs": {}, "artists": {}, "albums": {}}
        self.lock = threading.Lock()
        if cache_path and cache_path.exists():
            try:
                self.cache.update(json.loads(cache_path.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                pass

    def save(self) -> None:
        if self.cache_path:
            with self.lock:
                tmp = self.cache_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")
                tmp.replace(self.cache_path)

    # ----- public -----
    def lookup(self, artist: str, title: str) -> dict:
        key = " | ".join(song_key(artist, title))
        if key in self.cache["songs"]:
            return dict(self.cache["songs"][key])
        info = dict(EMPTY, tags=[])
        failed = []  # a source that is down shouldn't lose what the other one found
        try:
            dz = self._deezer(artist, title)
        except Exception as e:
            dz, failed = {}, failed + [f"Deezer: {e}"]
        info.update({k: dz[k] for k in ("genre", "bpm", "year") if dz.get(k)})
        dz_artist = dz.get("artist") or artist
        mb = {}
        try:
            mb = self.artist_info(artist) if artist else {}
            if not mb.get("found") and dz_artist and dz_artist != artist:
                mb = self.artist_info(dz_artist)
        except Exception as e:
            failed.append(f"MusicBrainz: {e}")
        info["tags"] = mb.get("tags", [])
        info["country"] = mb.get("country", "")
        info["cyrillic"] = any(has_cyrillic(s) for s in (artist, title, dz.get("title"), dz_artist, mb.get("name")))
        if not info["genre"] and info["tags"]:
            info["genre"] = info["tags"][0].title()
        if failed:
            if not dz and not mb:
                raise RuntimeError("; ".join(failed))
            return dict(info)  # partial: not cached, so it is looked up again next time
        with self.lock:
            self.cache["songs"][key] = info
        return dict(info)

    def artist_info(self, artist: str) -> dict:
        akey = norm_artist(artist)
        if akey in self.cache["artists"]:
            return self.cache["artists"][akey]
        main = re.split(r"\s*(?:,|&| feat\.? | ft\.? | x )\s*", artist, maxsplit=1)[0]
        data = self.fetch("https://musicbrainz.org/ws/2/artist/?fmt=json&limit=5&query="
                          + urllib.parse.quote(main), _MUSICBRAINZ) or {}
        best = None
        for a in data.get("artists", []):
            if a.get("score", 0) >= 80 and similar(main, a.get("name", "")) >= 0.85:
                best = a
                break
            for alias in a.get("aliases", []) or []:
                if a.get("score", 0) >= 80 and similar(main, alias.get("name", "")) >= 0.9:
                    best = a
                    break
            if best:
                break
        info = {"found": False, "tags": [], "country": "", "name": ""}
        if best:
            tags = sorted(best.get("tags", []) or [], key=lambda t: -t.get("count", 0))
            names = [t["name"] for t in tags if not t["name"].startswith("_") and not _DECADE_TAG.match(t["name"])]
            country = best.get("country") or ""
            if not country:
                codes = (best.get("area") or {}).get("iso-3166-1-codes") or []
                country = codes[0] if codes else ""
            info = {"found": True, "tags": names[:6], "country": country, "name": best.get("name", "")}
        with self.lock:
            self.cache["artists"][akey] = info
        return info

    # ----- Deezer -----
    def _deezer(self, artist: str, title: str) -> dict:
        q = urllib.parse.quote(f"{artist} {_BRACKETS.sub('', title)}".strip())
        data = self.fetch(f"https://api.deezer.com/search?q={q}&limit=8", _DEEZER) or {}
        best, best_score = None, 0.0
        for i, r in enumerate(data.get("data", [])):
            rt, ra = r.get("title", ""), (r.get("artist") or {}).get("name", "")
            t_sim = max(similar(title, rt), similar(_BRACKETS.sub("", title), _BRACKETS.sub("", rt)))
            a_sim = similar(artist, ra) if artist else 0.6
            if artist and a_sim < 0.6:
                a_sim = max(a_sim, similar(norm_artist(artist), norm_artist(ra)))
            score = t_sim + a_sim - i * 0.02
            if t_sim >= 0.7 and a_sim >= 0.6 and score > best_score:
                best, best_score = r, score
        if not best:
            return {}
        out = {"title": best.get("title"), "artist": (best.get("artist") or {}).get("name")}
        track = self.fetch(f"https://api.deezer.com/track/{best['id']}", _DEEZER) or {}
        if track.get("bpm"):
            out["bpm"] = round(float(track["bpm"]))
        date = track.get("release_date") or ""
        if date[:4].isdigit() and date[:4] != "0000":
            out["year"] = int(date[:4])
        album_id = str((best.get("album") or {}).get("id") or "")
        if album_id:
            if album_id not in self.cache["albums"]:
                album = self.fetch(f"https://api.deezer.com/album/{album_id}", _DEEZER) or {}
                self.cache["albums"][album_id] = [g["name"] for g in (album.get("genres") or {}).get("data", [])]
            genres = self.cache["albums"][album_id]
            if genres:
                out["genre"] = genres[0]
        return out


# ---------- describing a song for sorting ----------
def tempo_label(bpm, labels: dict | None = None) -> str:
    from .i18n import labels as default_labels
    lb = labels or default_labels()
    if not bpm:
        return lb["unknown_tempo"]
    if bpm < 100:
        return lb["easy"]
    if bpm < 120:
        return lb["steady"]
    if bpm < 140:
        return lb["fast"]
    return lb["sprint"]


def decade_label(year, labels: dict | None = None) -> str:
    from .i18n import labels as default_labels
    return f"{year // 10 * 10}s" if year else (labels or default_labels())["unknown_year"]


def facts(info: dict, artist: str = "") -> list[str]:
    """Everything a sorting rule can match on, lower case."""
    out = [info.get("genre", ""), *info.get("tags", [])]
    if info.get("country"):
        out += [info["country"], country_name(info["country"])]
    if info.get("cyrillic"):
        out.append("cyrillic")
    if artist:
        out.append(artist)
    return [f.lower() for f in out if f]


# ---------- measuring tempo from the audio ----------
def estimate_bpm(path, ffmpeg: str, seconds: int = 60) -> int | None:
    """Rough tempo of a song: listen to a minute from the middle, find the beat.
    Good enough to tell easy / steady / fast / sprint apart."""
    import array
    import subprocess
    rate = 8000
    try:
        dur = float(_probe_duration(path))
    except Exception:
        dur = 0
    start = max(0.0, dur / 2 - seconds / 2) if dur > seconds + 10 else 0.0
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    out = subprocess.run([ffmpeg, "-v", "error", "-ss", f"{start:.1f}", "-t", str(seconds), "-i", str(path),
                          "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
                         capture_output=True, creationflags=flags).stdout
    samples = array.array("h", out[: len(out) // 2 * 2])
    hop = 80  # 10 ms frames
    n = len(samples) // hop
    if n < 1000:  # less than 10 s of audio
        return None
    energy = [sum(abs(x) for x in samples[i * hop:(i + 1) * hop]) for i in range(n)]
    onset = [max(0, energy[i] - energy[i - 1]) for i in range(1, n)]
    mean = sum(onset) / len(onset)
    onset = [o - mean for o in onset]
    best_lag, best_val = None, 0.0
    scores = {}
    for lag in range(int(6000 / 200), int(6000 / 60) + 1):  # 200..60 BPM, frames of 10 ms
        v = sum(onset[i] * onset[i - lag] for i in range(lag, len(onset)))
        scores[lag] = v
        if v > best_val:
            best_lag, best_val = lag, v
    if not best_lag:
        return None
    bpm = 6000 / best_lag
    # prefer the double tempo when it is almost as strong (common half-tempo mistake)
    half = round(best_lag / 2)
    if bpm < 90 and half in scores and scores[half] > 0.6 * best_val:
        bpm *= 2
    return int(round(bpm))


def _probe_duration(path) -> float:
    from mutagen import File
    f = File(path)
    return f.info.length if f is not None and f.info else 0
