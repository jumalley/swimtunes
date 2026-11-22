"""Connecting YouTube and Spotify accounts (all network answers are simulated)."""
import os
import stat
import time
import urllib.parse

import pytest

from swimtunes import accounts, spotify
from swimtunes.downloader import Downloader
from swimtunes.library import Library
from swimtunes.web import create_app
from test_swimtunes import FakeDownloader


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SWIMTUNES_HOME", str(tmp_path / "home"))
    accounts._pending.clear()
    return tmp_path / "home"


@pytest.fixture
def lib(tmp_path):
    return Library(tmp_path / "lib")


# ---------- YouTube ----------
def test_youtube_browser_login_is_given_to_ytdlp(lib):
    assert accounts.ytdlp_options() == {}
    accounts.set_youtube_browser("firefox")
    assert accounts.ytdlp_options() == {"cookiesfrombrowser": ("firefox", None, None, None)}
    ydl = Downloader(lib, log=lambda _: None)._ydl()
    assert ydl.params["cookiesfrombrowser"] == ("firefox", None, None, None)
    with pytest.raises(ValueError):
        accounts.set_youtube_browser("netscape")


def test_youtube_cookies_file_is_private(home):
    with pytest.raises(ValueError):
        accounts.set_youtube_cookies("hello")
    accounts.set_youtube_cookies("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tsecret\n")
    p = accounts.cookies_path()
    assert accounts.ytdlp_options() == {"cookiefile": str(p)}
    if os.name != "nt":
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
    st = accounts.status()
    assert st["youtube"] == {"browser": "", "profile": "", "cookies_file": True, "connected": True}
    accounts.disconnect_youtube()
    assert not p.exists() and accounts.ytdlp_options() == {}


@pytest.mark.parametrize("text,kind", [
    ("ERROR: Could not copy Chrome cookie database. See https://github.com/yt-dlp/yt-dlp/issues/7271", "browser_open"),
    ("ERROR: Failed to decrypt with DPAPI. See https://github.com/yt-dlp/yt-dlp/issues/10927", "browser_locked"),
    ("could not find firefox cookies database in /x", "browser_missing"),
    ("something else", "other"),
])
def test_browser_errors_are_explained(text, kind):
    assert accounts.explain_youtube_error(text) == kind


def test_private_youtube_playlist_says_to_connect(lib):
    class Private(FakeDownloader):
        def list_entries(self, url):
            raise RuntimeError("ERROR: [youtube:tab] PLKF-C70hTcIE: The playlist does not exist.")
    c = create_app(lib, downloader_cls=Private).test_client()
    jid = c.post("/api/preview", json={"urls": "https://www.youtube.com/playlist?list=PLKF-C70hTcIE"}).get_json()["id"]
    for _ in range(200):
        j = c.get(f"/api/jobs/{jid}").get_json()
        if j["status"] in ("done", "error"):
            break
        time.sleep(0.02)
    assert j["status"] == "error" and j["log"][-1]["k"] == "err.yt_private"
    accounts.set_youtube_browser("firefox")
    jid = c.post("/api/preview", json={"urls": "https://www.youtube.com/playlist?list=PLKF-C70hTcIE"}).get_json()["id"]
    for _ in range(200):
        j = c.get(f"/api/jobs/{jid}").get_json()
        if j["status"] in ("done", "error"):
            break
        time.sleep(0.02)
    assert j["log"][-1]["k"] == "err.yt_private_connected"


def test_unavailable_videos_get_a_reason(lib):
    class Details(FakeDownloader):
        def video_info(self, vid):
            raise RuntimeError("\x1b[0;31mERROR:\x1b[0m [youtube] x: Private video. Sign in if you've been granted access")
    it = {"video_id": "x"}
    Details(lib, log=lambda _: None).add_details(it)
    assert it["unavailable_kind"] == "private" and it["unavailable"].startswith("[youtube] x: Private video")


# ---------- Spotify ----------
class FakeSpotify:
    """Simulates accounts.spotify.com and api.spotify.com."""
    def __init__(self):
        self.calls = []

    def __call__(self, url, *, data=None, token=None, timeout=20):
        self.calls.append((url, data, token))
        if url == "https://accounts.spotify.com/api/token":
            if data["grant_type"] == "authorization_code":
                assert data["code"] == "CODE" and len(data["code_verifier"]) >= 43
                return {"access_token": "AT1", "refresh_token": "RT1", "expires_in": 3600}
            assert data["grant_type"] == "refresh_token" and data["refresh_token"] == "RT1"
            return {"access_token": "AT2", "expires_in": 3600}
        assert token in ("AT1", "AT2")
        if url.endswith("/v1/me"):
            return {"display_name": "Jules", "id": "jules"}
        if "/v1/playlists/PRIV" in url and "offset" not in url:
            items = [{"track": {"name": f"Song {i}", "duration_ms": 200000, "type": "track",
                                "artists": [{"name": "Kino"}], "album": {"images": [{"url": "https://img/640", "width": 640},
                                                                                     {"url": "https://img/300", "width": 300}]}}}
                     for i in range(100)]
            return {"name": "My private mix", "tracks": {"items": items + [{"track": None}],
                                                         "next": "https://api.spotify.com/v1/playlists/PRIV/tracks?offset=100"}}
        if "/v1/playlists/PRIV/tracks?offset=100" in url:
            return {"items": [{"track": {"name": "Song 100", "duration_ms": 1000, "type": "track", "artists": [{"name": "Kino"}],
                                         "album": {"images": []}}},
                              {"track": {"name": "Local file", "is_local": True, "type": "track"}}], "next": None}
        if "/v1/me/tracks" in url:
            return {"items": [{"track": {"name": "Sudno", "duration_ms": 1000, "type": "track", "artists": [{"name": "Molchat Doma"}]}}],
                    "next": None}
        if "/v1/me/playlists" in url:
            return {"items": [{"name": "My private mix", "id": "PRIV", "public": False, "tracks": {"total": 101},
                               "external_urls": {"spotify": "https://open.spotify.com/playlist/PRIV"}, "images": []}], "next": None}
        raise AssertionError(url)


def connect_spotify(monkeypatch):
    fake = FakeSpotify()
    monkeypatch.setattr(accounts, "_http", fake)
    url = accounts.spotify_start("0123456789abcdef0123456789abcdef", "http://127.0.0.1:8765/spotify/callback")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["code_challenge_method"] == ["S256"] and "playlist-read-private" in q["scope"][0]
    assert q["redirect_uri"] == ["http://127.0.0.1:8765/spotify/callback"]
    assert accounts.spotify_finish("CODE", q["state"][0]) == "Jules"
    return fake


def test_spotify_login_and_private_playlist(monkeypatch):
    fake = connect_spotify(monkeypatch)
    st = accounts.status()
    assert st["spotify"] == {"client_id": "0123456789abcdef0123456789abcdef", "connected": True, "user": "Jules"}
    assert "RT1" not in str(st) and "AT1" not in str(st)          # secrets never leave the server
    data = spotify.fetch("https://open.spotify.com/playlist/PRIV?si=abc")
    assert data["name"] == "My private mix" and len(data["tracks"]) == 101     # more than 100, local file skipped
    assert data["tracks"][0] == {"title": "Song 0", "artist": "Kino", "duration": 200.0, "cover": "https://img/300"}
    assert spotify.fetch(spotify.LIKED)["tracks"][0]["title"] == "Sudno"
    pls = accounts.spotify_my_playlists()
    assert pls[0]["url"] == spotify.LIKED and pls[1]["private"] and pls[1]["count"] == 101
    with pytest.raises(ValueError):
        accounts.spotify_finish("CODE", "wrong-state")


def test_spotify_token_is_refreshed(monkeypatch):
    connect_spotify(monkeypatch)
    a = accounts.load()
    a["spotify"]["expires_at"] = 0
    accounts.save(a)
    assert accounts.spotify_token() == "AT2" and accounts.load()["spotify"]["refresh_token"] == "RT1"


def test_spotify_without_account_uses_public_page_and_explains_private(monkeypatch):
    monkeypatch.setattr(spotify.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    with pytest.raises(spotify.SpotifyError):
        spotify.fetch("https://open.spotify.com/playlist/PRIV")
    with pytest.raises(spotify.SpotifyError, match="Connect your Spotify"):
        spotify.fetch(spotify.LIKED)


def test_accounts_endpoints(lib, monkeypatch):
    c = create_app(lib).test_client()
    assert c.get("/api/accounts").get_json()["youtube"]["connected"] is False
    assert c.post("/api/accounts/youtube", json={"browser": "firefox"}).get_json()["youtube"]["browser"] == "firefox"
    r = c.post("/api/accounts/youtube/cookies", json={"text": "nope"})
    assert r.status_code == 400 and r.get_json()["key"] == "err.bad_cookies"
    assert c.post("/api/accounts/spotify/start", json={"client_id": "x"}).get_json()["key"] == "err.bad_client_id"
    r = c.post("/api/accounts/spotify/start", json={"client_id": "0123456789abcdef0123456789abcdef"}).get_json()
    assert r["url"].startswith("https://accounts.spotify.com/authorize?") and r["redirect"].endswith("/spotify/callback")
    page = c.get("/spotify/callback?error=access_denied")
    assert page.status_code == 200 and b"access_denied" in page.data
    monkeypatch.setattr(accounts, "_http", FakeSpotify())
    state = urllib.parse.parse_qs(urllib.parse.urlparse(r["url"]).query)["state"][0]
    page = c.get(f"/spotify/callback?code=CODE&state={state}")
    assert b"Jules" in page.data and c.get("/api/accounts").get_json()["spotify"]["connected"]
    c.delete("/api/accounts/spotify")
    st = c.get("/api/accounts").get_json()["spotify"]
    assert st["connected"] is False and st["client_id"]          # the Client ID is kept for next time


def test_spotify_2026_format_and_fallback(monkeypatch):
    """Spotify renamed playlist "tracks" to "items" in 2026; some playlists come back empty
    through the API: then the public page is used."""
    connect_spotify(monkeypatch)
    song = {"name": "Blinding Lights", "duration_ms": 200040, "type": "track", "artists": [{"name": "The Weeknd"}]}

    def api(url, *, data=None, token=None, timeout=20):
        if "/v1/playlists/NEW" in url:
            return {"name": "Top", "items": {"items": [{"item": song}], "next": None}}
        if "/v1/playlists/EMPTY" in url:
            return {"name": "Top 500", "items": {"items": [], "next": None}}
        if "/v1/playlists/GONE" in url:
            raise accounts.SpotifyAuthError(404, {"error": {"message": "Not found"}})
        raise AssertionError(url)
    monkeypatch.setattr(accounts, "_http", api)
    assert spotify.fetch("https://open.spotify.com/playlist/NEW")["tracks"][0]["title"] == "Blinding Lights"

    html = ('<script id="__NEXT_DATA__" type="application/json">{"props":{"entity":{"name":"Top 500","trackList":'
            '[{"title":"Shape of You","subtitle":"Ed Sheeran","duration":233712}]}}}</script>')

    class Page:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return html.encode()
    monkeypatch.setattr(spotify.urllib.request, "urlopen", Page)
    data = spotify.fetch("https://open.spotify.com/playlist/EMPTY")
    assert data["name"] == "Top 500" and data["tracks"][0]["artist"] == "Ed Sheeran"
    assert spotify.fetch("https://open.spotify.com/playlist/GONE")["tracks"]      # public page still works
    html = "<html>nothing</html>"
    with pytest.raises(spotify.SpotifyError, match="can't find"):
        spotify.fetch("https://open.spotify.com/playlist/GONE")
