"""Connected accounts: YouTube (through the user's browser login) and Spotify (official login).

Secrets live in a private file in the user's home folder (~/.swimtunes/accounts.json),
not in the music folder, and are never sent to the dashboard.

YouTube no longer lets programs log in with a password, so SwimTunes uses the login the
user already has in a web browser on this computer (or an exported cookies.txt file).

Spotify uses its official login ("Authorization Code with PKCE"). Every app needs a free
Client ID from https://developer.spotify.com/dashboard; the user creates one once.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BROWSERS = ["firefox", "chrome", "edge", "brave", "opera", "vivaldi", "chromium", "safari", "whale"]
SPOTIFY_SCOPES = "playlist-read-private playlist-read-collaborative user-library-read"
_lock = threading.Lock()
_pending: dict[str, dict] = {}   # Spotify logins in progress: state -> {verifier, redirect, client_id}


def app_dir() -> Path:
    d = Path(os.environ.get("SWIMTUNES_HOME") or Path.home() / ".swimtunes")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path() -> Path:
    return app_dir() / "accounts.json"


def load() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(data: dict) -> None:
    with _lock:
        p = _path()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)   # only this user can read it
        except OSError:
            pass
        tmp.replace(p)


def status() -> dict:
    """What the dashboard may know: connected or not, never the secrets."""
    a = load()
    yt, sp = a.get("youtube") or {}, a.get("spotify") or {}
    return {
        "youtube": {"browser": yt.get("browser") or "", "profile": yt.get("profile") or "",
                    "cookies_file": bool(yt.get("cookies_file")) and cookies_path().exists(),
                    "connected": bool(yt.get("browser")) or (bool(yt.get("cookies_file")) and cookies_path().exists())},
        "spotify": {"client_id": sp.get("client_id") or "", "connected": bool(sp.get("refresh_token")),
                    "user": sp.get("user") or ""},
        "browsers": BROWSERS,
        "platform": sys.platform,
    }


# ---------- YouTube ----------
def cookies_path() -> Path:
    return app_dir() / "youtube-cookies.txt"


def set_youtube_browser(browser: str, profile: str = "") -> None:
    browser = (browser or "").strip().lower()
    if browser and browser not in BROWSERS:
        raise ValueError(f"Unknown browser: {browser}")
    a = load()
    yt = a.setdefault("youtube", {})
    yt["browser"], yt["profile"] = browser, (profile or "").strip()
    if browser:
        yt["cookies_file"] = False
    save(a)


def set_youtube_cookies(text: str) -> None:
    if "youtube.com" not in text and ".google." not in text:
        raise ValueError("This doesn't look like a cookies.txt file with YouTube cookies")
    p = cookies_path()
    p.write_text(text, encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    a = load()
    yt = a.setdefault("youtube", {})
    yt.update(cookies_file=True, browser="", profile="")
    save(a)


def disconnect_youtube() -> None:
    a = load()
    a.pop("youtube", None)
    save(a)
    cookies_path().unlink(missing_ok=True)


def ytdlp_options() -> dict:
    """yt-dlp options that make it use the connected YouTube account."""
    yt = load().get("youtube") or {}
    if yt.get("browser"):
        return {"cookiesfrombrowser": (yt["browser"], yt.get("profile") or None, None, None)}
    if yt.get("cookies_file") and cookies_path().exists():
        return {"cookiefile": str(cookies_path())}
    return {}


def explain_youtube_error(text: str) -> str:
    """A short reason key for problems reading the browser login."""
    t = (text or "").lower()
    if "could not copy" in t and "cookie" in t:
        return "browser_open"          # Chrome/Edge keep the file locked while open
    if "dpapi" in t or "decrypt" in t or "app-bound" in t or "appbound" in t:
        return "browser_locked"        # Chrome/Edge on Windows encrypt cookies for themselves only
    if "could not find" in t and ("cookies" in t or "profile" in t or "browser" in t):
        return "browser_missing"
    if "sign in" in t or "login" in t or "not logged in" in t:
        return "not_signed_in"
    return "other"


# ---------- Spotify ----------
def _http(url: str, *, data: dict | None = None, token: str | None = None, timeout: float = 20):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    headers = {"User-Agent": "SwimTunes"}
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers), timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:   # too many requests: wait as told
                time.sleep(min(10, int(e.headers.get("Retry-After") or 2)))
                continue
            try:
                detail = json.load(e)
            except Exception:
                detail = {}
            raise SpotifyAuthError(e.code, detail) from None


class SpotifyAuthError(Exception):
    def __init__(self, code: int, detail: dict):
        self.code, self.detail = code, detail
        err = detail.get("error")
        text = (err.get("message") if isinstance(err, dict) else detail.get("error_description") or err) or ""
        super().__init__(f"Spotify said {code}: {text}".strip())


def spotify_start(client_id: str, redirect_uri: str) -> str:
    """URL of Spotify's login page (opened in the browser)."""
    client_id = (client_id or "").strip()
    if len(client_id) < 16:
        raise ValueError("Paste the Client ID from your Spotify app")
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    _pending[state] = {"verifier": verifier, "redirect": redirect_uri, "client_id": client_id}
    a = load()
    a.setdefault("spotify", {})["client_id"] = client_id
    save(a)
    return "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri, "state": state,
        "scope": SPOTIFY_SCOPES, "code_challenge_method": "S256", "code_challenge": challenge})


def spotify_finish(code: str, state: str) -> str:
    """Called when Spotify sends the user back. Returns the Spotify user name."""
    p = _pending.pop(state, None)
    if not p:
        raise ValueError("This Spotify login is no longer valid. Start again from SwimTunes.")
    tok = _http("https://accounts.spotify.com/api/token", data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": p["redirect"],
        "client_id": p["client_id"], "code_verifier": p["verifier"]})
    me = _http("https://api.spotify.com/v1/me", token=tok["access_token"])
    a = load()
    a["spotify"] = {"client_id": p["client_id"], "access_token": tok["access_token"],
                    "refresh_token": tok.get("refresh_token"), "expires_at": time.time() + int(tok.get("expires_in", 3600)) - 60,
                    "user": me.get("display_name") or me.get("id") or ""}
    save(a)
    return a["spotify"]["user"]


def disconnect_spotify() -> None:
    a = load()
    client_id = (a.get("spotify") or {}).get("client_id")
    a["spotify"] = {"client_id": client_id} if client_id else {}
    save(a)


def spotify_token() -> str | None:
    sp = load().get("spotify") or {}
    if not sp.get("refresh_token"):
        return None
    if sp.get("access_token") and time.time() < sp.get("expires_at", 0):
        return sp["access_token"]
    tok = _http("https://accounts.spotify.com/api/token", data={
        "grant_type": "refresh_token", "refresh_token": sp["refresh_token"], "client_id": sp["client_id"]})
    a = load()
    a["spotify"].update(access_token=tok["access_token"], expires_at=time.time() + int(tok.get("expires_in", 3600)) - 60)
    if tok.get("refresh_token"):
        a["spotify"]["refresh_token"] = tok["refresh_token"]
    save(a)
    return tok["access_token"]


def spotify_get(path_or_url: str) -> dict:
    token = spotify_token()
    if not token:
        raise RuntimeError("Spotify is not connected")
    url = path_or_url if path_or_url.startswith("http") else "https://api.spotify.com/v1" + path_or_url
    return _http(url, token=token)


def _image(images) -> str:
    """The ~300px picture from a Spotify image list."""
    images = images or []
    if not images:
        return ""
    best = min(images, key=lambda i: abs((i.get("width") or 300) - 300))
    return best.get("url") or ""


def _track(t: dict) -> dict | None:
    if not t or t.get("is_local") or t.get("type") not in (None, "track"):
        return None
    return {"title": t.get("name") or "", "artist": ", ".join(a["name"] for a in t.get("artists") or [] if a.get("name")),
            "duration": (t.get("duration_ms") or 0) / 1000, "cover": _image((t.get("album") or {}).get("images"))}


def spotify_collection(kind: str, sid: str) -> dict:
    """All songs of a playlist / album / track through Spotify's API (works for private playlists
    and has no 100-song limit). {'name', 'tracks': [{title, artist, duration, cover}]}"""
    if kind == "track":
        t = _track(spotify_get(f"/tracks/{sid}"))
        return {"name": t["title"] if t else "Spotify", "tracks": [t] if t else []}
    if kind == "album":
        album = spotify_get(f"/albums/{sid}")
        cover = _image(album.get("images"))
        tracks, page = [], album.get("tracks") or {}
        while True:
            for t in page.get("items") or []:
                t = dict(t, album={"images": album.get("images")})
                if (x := _track(t)):
                    tracks.append(x)
            if not page.get("next"):
                break
            page = spotify_get(page["next"])
        return {"name": album.get("name") or "Spotify", "tracks": tracks, "cover": cover}
    pl = spotify_get(f"/playlists/{sid}")
    # Spotify renamed "tracks" to "items" (and each entry's "track" to "item") in 2026: accept both.
    page = pl.get("items") if isinstance(pl.get("items"), dict) else pl.get("tracks") or {}
    tracks = []
    while True:
        for entry in page.get("items") or []:
            if (x := _track((entry or {}).get("item") or (entry or {}).get("track"))):
                tracks.append(x)
        if not page.get("next"):
            break
        page = spotify_get(page["next"])
    return {"name": pl.get("name") or "Spotify", "tracks": tracks}


def spotify_my_playlists() -> list[dict]:
    out, url = [], "/me/playlists?limit=50"
    while url:
        page = spotify_get(url)
        for p in page.get("items") or []:
            if not p:
                continue
            out.append({"name": p.get("name") or "", "url": (p.get("external_urls") or {}).get("spotify") or
                        f"https://open.spotify.com/playlist/{p.get('id')}", "count": (p.get("items") if isinstance(p.get("items"), dict) else p.get("tracks") or {}).get("total"),
                        "image": _image(p.get("images")), "private": not p.get("public", True), "source": "spotify"})
        url = page.get("next")
    out.insert(0, {"name": "Liked Songs", "url": "spotify:collection:tracks", "count": None, "image": "",
                   "private": True, "source": "spotify", "liked": True})
    return out


def spotify_liked() -> dict:
    tracks, url = [], "/me/tracks?limit=50"
    while url:
        page = spotify_get(url)
        for item in page.get("items") or []:
            if (x := _track(item.get("item") or item.get("track"))):
                tracks.append(x)
        url = page.get("next")
    return {"name": "Liked Songs", "tracks": tracks}
