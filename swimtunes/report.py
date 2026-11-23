"""A problem report to copy and paste when something goes wrong.

Only what helps find the problem: versions, system, the health checks, recent errors and
the end of the logs. Personal details are left out or hidden: the home folder becomes "~",
Bluetooth addresses and logins are never included, no song titles from the logs' paths
beyond what the errors themselves say. Nothing is sent anywhere; the user copies it.
"""

from __future__ import annotations

import os
import platform
import re
import time
from pathlib import Path

from . import TAG, __version__, accounts, tools, updater

MAC_RE = re.compile(r"\b([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
TOKEN_RE = re.compile(r"(?i)(token|secret|password|cookie|code_verifier|authorization)([\"'=:\s]+)[^\s\"',]+")


def _private(text: str) -> str:
    home = str(Path.home())
    text = text.replace(home, "~")
    if os.name == "nt":
        text = text.replace(home.replace("\\", "/"), "~")
    text = MAC_RE.sub("XX:XX:XX:XX:XX:XX", text)
    return TOKEN_RE.sub(r"\1\2[hidden]", text)


def _tail(path: Path, lines: int = 40) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [line.rstrip("\n") for line in f.readlines()[-lines:]]
    except OSError:
        return []


def build(lib, jobs: dict | None = None) -> str:
    cfg = lib.config()
    out = ["SwimTunes problem report", "=" * 24,
           f"Version: {TAG if TAG != 'dev' else __version__} ({updater.install_kind()})",
           f"System: {platform.platform()} · Python {platform.python_version()} · {'app' if tools.is_frozen() else 'source'}",
           f"Language: {cfg.get('language')} · Made: {time.strftime('%Y-%m-%d %H:%M')}", ""]
    out.append("Health:")
    for row in tools.health():
        out.append(f"  {'OK ' if row['ok'] else 'NO '} {row['name']}: {row.get('detail', '')}")
    st = lib.stats()
    out += ["", f"Library: {st['count']} songs in {len(st['folders'])} folders · earphones size setting {cfg['capacity_gb']} GB",
            f"Settings: bitrate {cfg['bitrate']} · normalize {cfg['normalize']} · duplicates {cfg['dupe_scope']} · "
            f"sort {cfg['sort_mode']} · auto-update {cfg.get('auto_update')} · channel {cfg.get('update_channel', 'stable')}",
            f"Followed playlists: {len(cfg.get('followed') or [])} · rules: {len(cfg.get('rules') or [])} · "
            f"YouTube helper: {'on' if cfg.get('youtube_helper') else 'off'}"]
    acc = accounts.status()
    out.append(f"Accounts: YouTube {'connected (' + (acc['youtube']['browser'] or 'cookies file') + ')' if acc['youtube']['connected'] else 'not connected'}"
               f" · Spotify {'connected' if acc['spotify']['connected'] else 'not connected'}")
    sh = cfg.get("shokz") or {}
    if sh:
        out.append(f"Earphones (Bluetooth): {sh.get('name', '?')} · firmware {sh.get('firmware', '?')} · battery {sh.get('battery', '?')}%")
    errors = []
    for j in sorted((jobs or {}).values(), key=lambda j: j["started"]):
        for e in j["log"]:
            if e.get("error") or str(e.get("k", "")).startswith(("err.", "log.yt_login")):
                errors.append(f"  [{j['kind']}] {e.get('t', '')}")
    out += ["", "Recent errors:"] + (errors[-15:] or ["  none"])
    logs = [lib.root / ".swimtunes.log"]
    if os.environ.get("SWIMTUNES_LAUNCHER"):
        home = Path(os.environ.get("SWIMTUNES_LAUNCHER_HOME", ""))
        logs += [home / "launcher.log", home / "swimtunes.log"]
    for log in logs:
        lines = _tail(log)
        if lines:
            out += ["", f"End of {log.name}:"] + [f"  {line}" for line in lines]
    return _private("\n".join(out)) + "\n"
