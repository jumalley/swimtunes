"""Optional "YouTube token helper" for downloads YouTube refuses ("Sign in to confirm you're not a bot", 403).

YouTube increasingly asks for a proof-of-origin (PO) token. The open-source bgutil project
(https://github.com/Brainicism/bgutil-ytdlp-pot-provider, GPL-3.0) makes them: a small local
server plus a yt-dlp plugin. It is NOT shipped with SwimTunes (license, ~230 MB). When the
user turns it on (Settings), it is downloaded onto their computer, checked against pinned
fingerprints, and installed with the Deno that SwimTunes already includes. SwimTunes then
starts the helper on 127.0.0.1 when a download needs it and passes it to yt-dlp.

Files: ~/.swimtunes/youtube-helper/ (plugins/, server/, installed.json, helper.log).
"""

from __future__ import annotations

import atexit
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from . import accounts, tools
from .i18n import msg

VERSION = "2.0.2"
PLUGIN_URL = ("https://files.pythonhosted.org/packages/fa/ac/9da2e48eb58bbeb2c56739eed37c0e4c17b3c20c4c53a49c5a62c47a1148/"
              "bgutil_ytdlp_pot_provider-2.0.2-py3-none-any.whl")
PLUGIN_SHA256 = "4f488008d25a715837df79472aad06ea052d20e14ad3a02609bbb0fbd746b8be"
SOURCE_URL = f"https://github.com/Brainicism/bgutil-ytdlp-pot-provider/archive/refs/tags/{VERSION}.zip"
SERVER_FINGERPRINT = "03036eee15340bea17047d0127ed33cc7c70b3531c5b11671060d6bbd258d2ae"   # all files of server/
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_port: int | None = None
_plugins_loaded = False


def home() -> Path:
    return accounts.app_dir() / "youtube-helper"


def installed() -> bool:
    try:
        return json.loads((home() / "installed.json").read_text()).get("version") == VERSION
    except (OSError, ValueError):
        return False


def status() -> dict:
    return {"installed": installed(), "running": running(), "version": VERSION,
            "size_mb": _size_mb() if installed() else 0}


def _size_mb() -> int:
    total = 0
    for root, _, files in os.walk(home()):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return round(total / 1e6)


def fingerprint(server_dir: Path) -> str:
    """One digest over every file of the helper's server folder (node_modules left out)."""
    rows = []
    for root, dirs, files in os.walk(server_dir):
        dirs[:] = [d for d in dirs if d != "node_modules"]
        for f in files:
            p = Path(root) / f
            rel = p.relative_to(server_dir).as_posix()
            rows.append(f"{rel}\0{hashlib.sha256(p.read_bytes()).hexdigest()}\n")
    return hashlib.sha256("".join(sorted(rows)).encode()).hexdigest()


def _download(url: str, limit: int = 50_000_000) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "SwimTunes"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read(limit)


def install(log: Callable[[str], None] = print, fetch=_download, run=None) -> None:
    """Download, check and set up the helper (about a minute, ~230 MB)."""
    deno = tools.deno_path()
    if not deno:
        raise RuntimeError(msg("err.helper_no_deno", "The YouTube helper needs Deno, which is missing. Reinstall SwimTunes."))
    stop()
    base = home()
    tmp = base.with_name("youtube-helper.new")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        log(msg("log.helper_plugin", "Downloading the yt-dlp plugin…"))
        wheel = fetch(PLUGIN_URL)
        if hashlib.sha256(wheel).hexdigest() != PLUGIN_SHA256:
            raise RuntimeError(msg("err.helper_check", "The downloaded helper isn't the expected file. Nothing was installed."))
        with zipfile.ZipFile(io.BytesIO(wheel)) as z:
            for name in z.namelist():
                if name.startswith("yt_dlp_plugins/"):
                    z.extract(name, tmp / "plugins" / "bgutil")
        log(msg("log.helper_server", "Downloading the token helper…"))
        with zipfile.ZipFile(io.BytesIO(fetch(SOURCE_URL))) as z:
            prefix = next(n for n in z.namelist() if n.endswith("/server/"))
            for name in z.namelist():
                if name.startswith(prefix) and not name.endswith("/"):
                    dest = tmp / "server" / name[len(prefix):]
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(z.read(name))
        if fingerprint(tmp / "server") != SERVER_FINGERPRINT:
            raise RuntimeError(msg("err.helper_check", "The downloaded helper isn't the expected file. Nothing was installed."))
        log(msg("log.helper_deps", "Installing what the helper needs (about a minute, ~230 MB)…"))
        run = run or (lambda cmd, cwd: subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=900,
                                                      creationflags=NO_WINDOW))
        r = run([deno, "install", "--allow-scripts=npm:canvas", "--frozen"], tmp / "server")
        if r.returncode != 0:
            raise RuntimeError(msg("err.helper_install", "Couldn't install the YouTube helper: {error}",
                                   error=((r.stderr or r.stdout or "").strip().splitlines() or ["?"])[-1][:200]))
        (tmp / "installed.json").write_text(json.dumps({"version": VERSION, "time": time.time()}))
        shutil.rmtree(base, ignore_errors=True)
        tmp.rename(base)
        log(msg("log.helper_done", "YouTube helper installed"))
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def remove() -> None:
    stop()
    shutil.rmtree(home(), ignore_errors=True)


# ---------- running it ----------
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _answers(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def running() -> bool:
    return _proc is not None and _proc.poll() is None and _port is not None and _answers(_port)


def start(wait: float = 20.0) -> int | None:
    """Start the helper on 127.0.0.1 (once); returns its port, or None if it can't run."""
    global _proc, _port
    with _lock:
        if running():
            return _port
        if not installed() or not tools.deno_path():
            return None
        port = _free_port()
        modules = home() / "server" / "node_modules"
        logf = open(home() / "helper.log", "a", encoding="utf-8")
        _proc = subprocess.Popen(
            [tools.deno_path(), "run", "--allow-env", "--allow-net", "--allow-ffi=.", "--allow-read=.",
             "../src/main.ts", "--port", str(port)],
            cwd=modules, stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        _port = port
        end = time.time() + wait
        while time.time() < end:
            if _proc.poll() is not None:
                _proc, _port = None, None
                return None
            if _answers(port):
                return port
            time.sleep(0.3)
        stop()
        return None


def stop() -> None:
    global _proc, _port
    if _proc is not None and _proc.poll() is None:
        _proc.terminate()
        try:
            _proc.wait(5)
        except subprocess.TimeoutExpired:
            _proc.kill()
    _proc, _port = None, None


atexit.register(stop)


def ytdlp_options() -> dict:
    """Extra yt-dlp options when the helper is on: load its plugin and point it at the helper."""
    global _plugins_loaded
    port = start()
    if not port:
        return {}
    if not _plugins_loaded:
        import yt_dlp.globals as g
        from yt_dlp.plugins import load_all_plugins
        folder = str(home() / "plugins")
        if folder not in g.plugin_dirs.value:
            g.plugin_dirs.value = [*g.plugin_dirs.value, folder]
        load_all_plugins()
        _plugins_loaded = True
    return {"extractor_args": {"youtubepot-bgutilhttp": {"base_url": [f"http://127.0.0.1:{port}"]}}}
