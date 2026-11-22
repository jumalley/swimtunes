"""Read the track list of a public Spotify playlist / album / track link.

Uses Spotify's public embed page (the one used for "embed this playlist" on
websites), so no Spotify account or API key is needed. Large playlists may be
cut off by the embed page (~100 tracks); for those, convert the playlist to
YouTube with playlistbuddy.com and paste the YouTube playlist link instead.
"""

from __future__ import annotations

import json
import re
import urllib.request

SPOTIFY_RE = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[\w-]+/)?(playlist|album|track)/|spotify:(playlist|album|track):)([A-Za-z0-9]+)"
)
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"


class SpotifyError(Exception):
    pass


def is_spotify(url: str) -> bool:
    return bool(SPOTIFY_RE.search(url))


def parse_url(url: str) -> tuple[str, str]:
    m = SPOTIFY_RE.search(url)
    if not m:
        raise SpotifyError(f"Not a Spotify link: {url}")
    return (m.group(1) or m.group(2)), m.group(3)


def _find_entity(obj):
    """Locate the dict describing the playlist/album/track anywhere in the page JSON."""
    if isinstance(obj, dict):
        if "trackList" in obj or (obj.get("type") == "track" and ("artists" in obj or "subtitle" in obj)):
            return obj
        for v in obj.values():
            if (found := _find_entity(v)) is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            if (found := _find_entity(v)) is not None:
                return found
    return None


def _artists(item: dict) -> str:
    if item.get("artists"):
        return ", ".join(a.get("name", "") for a in item["artists"] if a.get("name"))
    return (item.get("subtitle") or "").replace(" ", " ")


def parse_embed_html(html: str) -> dict:
    m = _NEXT_DATA.search(html)
    if not m:
        raise SpotifyError("Couldn't read the Spotify page (format changed?). "
                           "Convert it with playlistbuddy.com and paste the YouTube link instead.")
    entity = _find_entity(json.loads(m.group(1)))
    if entity is None:
        raise SpotifyError("No tracks found on the Spotify page. Is the playlist public?")
    name = entity.get("name") or entity.get("title") or "Spotify"
    items = entity.get("trackList") or [entity]
    tracks = []
    for it in items:
        title = it.get("title") or it.get("name")
        if not title:
            continue
        dur = it.get("duration") or it.get("duration_ms") or 0
        if isinstance(dur, dict):
            dur = dur.get("totalMilliseconds", 0)
        tracks.append({"title": title, "artist": _artists(it), "duration": (dur or 0) / 1000})
    return {"name": name, "tracks": tracks}


def fetch(url: str, timeout: float = 20) -> dict:
    """Returns {'name': playlist name, 'tracks': [{'title', 'artist', 'duration'}]}."""
    kind, sid = parse_url(url)
    req = urllib.request.Request(f"https://open.spotify.com/embed/{kind}/{sid}", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            html = r.read().decode("utf-8", "replace")
    except OSError as e:
        raise SpotifyError(f"Couldn't reach Spotify: {e}") from e
    return parse_embed_html(html)
