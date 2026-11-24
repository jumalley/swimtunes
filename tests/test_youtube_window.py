"""Signing in to YouTube with a SwimTunes window (Chrome/Edge): the browser itself is simulated here.
A real-browser check is in docs/RELEASE_CHECKLIST.md."""
import time

import pytest
from helpers import wait

from swimtunes import accounts, ytlogin
from swimtunes.web import create_app

SIGNED_IN = [
    {"name": "SAPISID", "value": "abc", "domain": ".youtube.com", "path": "/", "secure": True, "expires": 2000000000},
    {"name": "SID", "value": "g1", "domain": ".google.com", "path": "/", "secure": True, "expires": 2000000000},
    {"name": "session", "value": "s", "domain": "www.youtube.com", "path": "/", "secure": False, "session": True},
    {"name": "tracker", "value": "x", "domain": ".example.com", "path": "/", "secure": True, "expires": 2000000000},
]


def test_cookies_txt_keeps_only_youtube_and_google():
    txt = ytlogin.to_cookies_txt(SIGNED_IN)
    rows = [line.split("\t") for line in txt.splitlines() if not line.startswith("#")]
    assert [r[5] for r in rows] == ["SAPISID", "SID", "session"]
    assert rows[0][:5] == [".youtube.com", "TRUE", "/", "TRUE", "2000000000"]
    assert rows[2][1] == "FALSE" and rows[2][4] == "0"          # host-only, session cookie
    assert txt.startswith("# Netscape HTTP Cookie File")


def test_export_saves_the_login_or_says_it_is_missing():
    with pytest.raises(ytlogin.NotSignedIn) as e:
        ytlogin.export(read=lambda: [c for c in SIGNED_IN if c["name"] != "SAPISID"])
    assert e.value.args[0].key == "err.yt_not_signed_in" and not accounts.status()["youtube"]["connected"]
    ytlogin.export(read=lambda: SIGNED_IN)
    st = accounts.status()["youtube"]
    assert st["connected"] and st["window"] and accounts.ytdlp_options() == {"cookiefile": str(accounts.cookies_path())}


def test_the_login_is_refreshed_when_old():
    calls = []

    def read():
        calls.append(1)
        return SIGNED_IN
    assert ytlogin.refresh_if_stale(read) is False                # not signed in with the window: nothing to do
    ytlogin.export(read)
    assert ytlogin.refresh_if_stale(read) is False and len(calls) == 1   # fresh
    a = accounts.load()
    a["youtube"]["exported"] = time.time() - ytlogin.REFRESH_AFTER - 1
    accounts.save(a)
    assert ytlogin.refresh_if_stale(read) is True and len(calls) == 2
    a = accounts.load()
    a["youtube"]["exported"] = 0
    accounts.save(a)
    assert ytlogin.refresh_if_stale(lambda: (_ for _ in ()).throw(OSError("browser gone"))) is False
    assert accounts.status()["youtube"]["connected"]              # a failed refresh keeps the saved login


def test_sign_in_button(lib, monkeypatch):
    class Window:                       # the user signs in, then closes the window
        def __init__(self):
            self.n = 0

        def poll(self):
            self.n += 1
            return 0                     # closed (signed in already)

        def terminate(self):
            pass
    monkeypatch.setattr(ytlogin, "find_browser", lambda: ("Chrome", "/x/chrome"))
    monkeypatch.setattr(ytlogin, "open_window", Window)
    monkeypatch.setattr(ytlogin, "read_cookies", lambda: SIGNED_IN)
    c = create_app(lib).test_client()
    assert c.get("/api/accounts/youtube/window").get_json()["browser"] == "Chrome"
    j = wait(c, c.post("/api/accounts/youtube/window").get_json()["id"])
    assert j["status"] == "done" and j["result"]["youtube"]["window"] is True
    assert [e["k"] for e in j["log"]] == ["log.yt_window_open", "log.yt_window_reading", "log.yt_window_done"]
    monkeypatch.setattr(ytlogin, "find_browser", lambda: None)
    assert c.post("/api/accounts/youtube/window").get_json()["key"] == "err.no_chrome"
