"""Sign in to YouTube in a SwimTunes window: the easy way to connect a YouTube account.

Reading the login of the user's everyday Chrome or Edge doesn't work on Windows: those browsers
encrypt it so only they can read it ("app-bound encryption"), and they lock it while open.
Instead, SwimTunes opens Chrome/Edge/Brave with a profile of its own (~/.swimtunes/youtube-browser).
The user signs in to YouTube there once and closes the window. SwimTunes then starts that
profile invisibly (headless) and asks the browser itself, over its local debugging connection,
for the YouTube cookies: the browser decrypts them for us. They are saved as the cookies.txt
SwimTunes already uses, and read again from the profile when they get old, so the login stays fresh.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from . import accounts
from .i18n import msg

LOGIN_URL = "https://accounts.google.com/ServiceLogin?service=youtube&continue=https%3A%2F%2Fwww.youtube.com%2F"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
LOGIN_COOKIES = ("SAPISID", "__Secure-3PAPISID", "LOGIN_INFO")   # only there once signed in
REFRESH_AFTER = 6 * 3600


class NotSignedIn(Exception):
    pass


def find_browser() -> tuple[str, str] | None:
    """(name, path) of an installed Chrome-like browser, or None."""
    if os.environ.get("SWIMTUNES_CHROME"):
        return "Chrome", os.environ["SWIMTUNES_CHROME"]
    if sys.platform == "win32":
        pf, pf86, local = (os.environ.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"))
        candidates = [("Chrome", rf"{d}\Google\Chrome\Application\chrome.exe") for d in (pf, pf86, local)]
        candidates += [("Edge", rf"{d}\Microsoft\Edge\Application\msedge.exe") for d in (pf86, pf)]
        candidates += [("Brave", rf"{d}\BraveSoftware\Brave-Browser\Application\brave.exe") for d in (pf, local)]
    elif sys.platform == "darwin":
        candidates = [("Chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                      ("Edge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
                      ("Brave", "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
                      ("Chromium", "/Applications/Chromium.app/Contents/MacOS/Chromium")]
    else:
        candidates = [(n, shutil.which(c) or "") for n, c in (("Chrome", "google-chrome"), ("Chrome", "google-chrome-stable"),
                      ("Chromium", "chromium"), ("Chromium", "chromium-browser"), ("Edge", "microsoft-edge"), ("Brave", "brave-browser"))]
    return next(((n, p) for n, p in candidates if p and os.path.isfile(p)), None)


def _extra_flags() -> list[str]:
    # Chrome refuses to start as Linux root without this (servers, containers); never needed on a PC
    return ["--no-sandbox"] if hasattr(os, "geteuid") and os.geteuid() == 0 else []


def profile_dir() -> Path:
    return accounts.app_dir() / "youtube-browser"


def open_window() -> subprocess.Popen:
    """Open the sign-in window (a separate browser window with SwimTunes' own profile)."""
    b = find_browser()
    if not b:
        raise RuntimeError(msg("err.no_chrome", "No Chrome, Edge or Brave on this computer. Use Firefox or a cookies.txt file instead."))
    profile_dir().mkdir(parents=True, exist_ok=True)
    return subprocess.Popen([b[1], f"--user-data-dir={profile_dir()}", "--no-first-run", "--no-default-browser-check",
                             *_extra_flags(), "--new-window", LOGIN_URL], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _local_json(url: str):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 127.0.0.1: never through a proxy
    with opener.open(url, timeout=2) as r:
        return json.load(r)


def read_cookies(timeout: float = 30.0) -> list[dict]:
    """All cookies of SwimTunes' browser profile, decrypted by the browser itself."""
    from websockets.sync.client import connect
    b = find_browser()
    if not b:
        raise RuntimeError(msg("err.no_chrome", "No Chrome, Edge or Brave on this computer. Use Firefox or a cookies.txt file instead."))
    port = _free_port()
    proc = subprocess.Popen([b[1], "--headless=new", f"--user-data-dir={profile_dir()}", f"--remote-debugging-port={port}",
                             "--remote-allow-origins=*", "--no-first-run", "--no-default-browser-check", *_extra_flags(),
                             "about:blank"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    try:
        end, ws_url = time.time() + timeout, None
        while time.time() < end and not ws_url:
            if proc.poll() is not None:
                raise RuntimeError(msg("err.yt_window_open", "Close the SwimTunes YouTube window first, then try again."))
            try:
                ws_url = _local_json(f"http://127.0.0.1:{port}/json/version")["webSocketDebuggerUrl"]
            except (OSError, ValueError, KeyError):
                time.sleep(0.3)
        if not ws_url:
            raise RuntimeError(msg("err.yt_window_slow", "The browser didn't answer. Try again."))
        with connect(ws_url, max_size=None, open_timeout=10, proxy=None) as ws:
            ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
            while True:
                answer = json.loads(ws.recv(timeout=15))
                if answer.get("id") == 1:
                    return answer.get("result", {}).get("cookies", [])
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


def to_cookies_txt(cookies: list[dict]) -> str:
    """YouTube and Google cookies in the cookies.txt format yt-dlp reads."""
    lines = ["# Netscape HTTP Cookie File", "# From SwimTunes' YouTube window"]
    for c in cookies:
        domain = c.get("domain", "")
        if not any(domain.endswith(d) for d in ("youtube.com", "google.com")):
            continue
        expires = int(c.get("expires") or 0) if not c.get("session") else 0
        lines.append("\t".join([domain, "TRUE" if domain.startswith(".") else "FALSE", c.get("path", "/"),
                                "TRUE" if c.get("secure") else "FALSE", str(max(expires, 0)), c["name"], c.get("value", "")]))
    return "\n".join(lines) + "\n"


def signed_in(cookies: list[dict]) -> bool:
    return any(c.get("name") in LOGIN_COOKIES and c.get("domain", "").endswith("youtube.com") for c in cookies)


def export(read=None) -> None:
    """Save the window's YouTube login for downloads (raises NotSignedIn if it isn't signed in)."""
    cookies = (read or read_cookies)()
    if not signed_in(cookies):
        raise NotSignedIn(msg("err.yt_not_signed_in", "You don't seem to be signed in to YouTube in the window. "
                              "Open it again, sign in, then close it."))
    accounts.set_youtube_cookies(to_cookies_txt(cookies))
    a = accounts.load()
    a.setdefault("youtube", {}).update(window=True, exported=time.time())
    accounts.save(a)


def refresh_if_stale(read=None) -> bool:
    """Read the login again when it's older than a few hours (YouTube renews its cookies)."""
    yt = accounts.load().get("youtube") or {}
    if not yt.get("window") or time.time() - (yt.get("exported") or 0) < REFRESH_AFTER:
        return False
    try:
        export(read)
        return True
    except Exception:
        return False        # keep the saved login; it may still work


def forget() -> None:
    """Sign out: remove SwimTunes' browser profile too."""
    shutil.rmtree(profile_dir(), ignore_errors=True)
