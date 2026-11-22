"""Keep SwimTunes up to date from GitHub, from inside the dashboard.

Three ways SwimTunes can be installed:
  * "app"  - the downloaded SwimTunes-Windows.exe / SwimTunes.app: download the newest
             release, swap it in, restart
  * "git"  - a `git clone` of the repository: git pull, reinstall requirements if they
             changed, restart
  * "pip"  - installed some other way: we can only point to the download page
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable

from . import REPO, TAG, __version__, tools

REPO_ROOT = Path(__file__).resolve().parent.parent
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def install_kind() -> str:
    if tools.is_frozen():
        return "app"
    if (REPO_ROOT / ".git").exists() and shutil.which("git"):
        return "git"
    return "pip"


def _git(*args, timeout=120) -> str:
    r = subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True,
                       timeout=timeout, creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout) else "git failed")
    return r.stdout.strip()


def _asset_name() -> str:
    if os.name == "nt":
        return "SwimTunes-Windows.exe"
    import platform
    return "SwimTunes-Mac-AppleSilicon.zip" if platform.machine() == "arm64" else "SwimTunes-Mac-Intel.zip"


def _latest_release() -> dict:
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "SwimTunes"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def _tag_tuple(tag: str) -> tuple:
    try:
        return tuple(int(x) for x in tag.lstrip("v").split("."))
    except ValueError:
        return ()


def check() -> dict:
    """Is there a newer version? Returns what changed, in plain words."""
    kind = install_kind()
    out = {"kind": kind, "current": TAG if TAG != "dev" else __version__, "available": False, "changes": [],
           "latest": None, "note": ""}
    try:
        if kind == "git":
            branch = _git("rev-parse", "--abbrev-ref", "HEAD")
            _git("fetch", "--quiet", "origin", branch)
            out["current"] = f"{__version__} ({_git('rev-parse', '--short', 'HEAD')})"
            log = _git("log", "--format=%s", f"HEAD..origin/{branch}")
            out["changes"] = [line for line in log.splitlines() if line][:30]
            out["available"] = bool(out["changes"])
            out["latest"] = _git("rev-parse", "--short", f"origin/{branch}")
            if _git("status", "--porcelain", "--untracked-files=no"):
                out["note"] = "You changed some SwimTunes files yourself; updating keeps your changes if they don't clash."
        else:
            rel = _latest_release()
            out["latest"] = rel.get("tag_name")
            out["available"] = (kind == "app" and _tag_tuple(out["latest"] or "") > _tag_tuple(TAG))
            out["changes"] = [l.strip("-* ").strip() for l in (rel.get("body") or "").splitlines() if l.strip()][:10]
            out["download"] = rel.get("html_url")
            if kind == "pip":
                out["note"] = "This copy can't update itself. Download the app from the releases page instead."
    except Exception as e:
        out["error"] = str(e)
    return out


def apply(log: Callable[[str], None] = print) -> list[str]:
    """Install the update. Returns the command that starts the new version."""
    kind = install_kind()
    if kind == "git":
        return _apply_git(log)
    if kind == "app":
        return _apply_app(log)
    raise RuntimeError("This copy of SwimTunes can't update itself. Download the newest app from GitHub.")


def _apply_git(log) -> list[str]:
    before = _git("rev-parse", "HEAD")
    log("Downloading the changes…")
    _git("pull", "--ff-only", timeout=300)
    changed = _git("diff", "--name-only", before, "HEAD").splitlines()
    if "pyproject.toml" in changed or not _deps_ok():
        log("Installing new requirements (this can take a minute)…")
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "-e", str(REPO_ROOT)],
                           capture_output=True, text=True, creationflags=NO_WINDOW)
        if r.returncode != 0:
            raise RuntimeError("Installing requirements failed: " + (r.stderr.strip().splitlines() or ["?"])[-1])
    log("Update installed.")
    return [sys.executable, "-m", "swimtunes", "ui", "--no-browser"]


def _deps_ok() -> bool:
    try:
        import deno  # noqa: F401
        import imageio_ffmpeg  # noqa: F401
        import yt_dlp_ejs  # noqa: F401
        return True
    except ImportError:
        return False


def _download(url: str, dest: Path, log) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "SwimTunes"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done, last = 0, -10
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            pct = int(done * 100 / total) if total else 0
            if pct >= last + 10:
                log(f"Downloading new version… {pct}%")
                last = pct


def _apply_app(log) -> list[str]:
    rel = _latest_release()
    asset = next((a for a in rel.get("assets", []) if a["name"] == _asset_name()), None)
    if not asset:
        raise RuntimeError(f"The newest release has no {_asset_name()}")
    exe = Path(sys.executable)
    if os.name == "nt":
        new = exe.with_name(exe.stem + ".new.exe")
        _download(asset["browser_download_url"], new, log)
        old = exe.with_name(exe.stem + ".old.exe")
        old.unlink(missing_ok=True)
        exe.rename(old)          # Windows allows renaming a running program
        new.rename(exe)
        log("Update installed.")
        return [str(exe)]
    # macOS: replace the whole SwimTunes.app
    app = exe.parents[2]
    if app.suffix != ".app":
        raise RuntimeError("Can't find SwimTunes.app")
    tmp = Path(tempfile.mkdtemp(prefix="swimtunes-update-"))
    z = tmp / "SwimTunes.zip"
    _download(asset["browser_download_url"], z, log)
    subprocess.run(["ditto", "-x", "-k", str(z), str(tmp)], check=True)
    new_app = tmp / "SwimTunes.app"
    old = app.with_name("SwimTunes.old.app")
    shutil.rmtree(old, ignore_errors=True)
    app.rename(old)
    shutil.move(str(new_app), str(app))
    subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(app)], capture_output=True)
    shutil.rmtree(tmp, ignore_errors=True)
    log("Update installed.")
    return [str(app / "Contents" / "MacOS" / "SwimTunes")]


def cleanup_old_versions() -> None:
    """Remove the previous version left behind by an update."""
    if not tools.is_frozen():
        return
    exe = Path(sys.executable)
    try:
        if os.name == "nt":
            exe.with_name(exe.stem + ".old.exe").unlink(missing_ok=True)
        elif exe.parents[2].suffix == ".app":
            shutil.rmtree(exe.parents[2].with_name("SwimTunes.old.app"), ignore_errors=True)
    except OSError:
        pass


def restart(cmd: list[str]) -> None:
    """Start the new version in the background (the old one is about to exit)."""
    env = dict(os.environ, SWIMTUNES_NO_BROWSER="1")
    if tools.is_frozen():
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"  # new copy must unpack itself, not reuse ours
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_CONSOLE
        subprocess.Popen(cmd, cwd=str(REPO_ROOT if not tools.is_frozen() else Path(cmd[0]).parent),
                         env=env, creationflags=flags, close_fds=True)
    else:
        subprocess.Popen(cmd, cwd=str(REPO_ROOT if not tools.is_frozen() else Path.home()), env=env,
                         start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True)
