"""Put a SwimTunes icon on the desktop that starts the dashboard."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import tools

STATIC = Path(__file__).resolve().parent / "static"
REPO_ROOT = Path(__file__).resolve().parent.parent


def _target() -> tuple[str, str, str]:
    """(program, arguments, start folder)"""
    if tools.is_frozen():
        return sys.executable, "", str(Path(sys.executable).parent)
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    prog = str(pythonw if pythonw.exists() else exe)  # pythonw = no black window; Quit from the page
    return prog, "-m swimtunes.app", str(REPO_ROOT)


def create_desktop_shortcut() -> str:
    prog, args, cwd = _target()
    if os.name == "nt":
        icon = sys.executable if tools.is_frozen() else str(STATIC / "icon.ico")
        ps = (
            "$d=[Environment]::GetFolderPath('Desktop');"
            "$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'SwimTunes.lnk'));"
            f"$s.TargetPath='{_ps(prog)}';$s.Arguments='{_ps(args)}';$s.WorkingDirectory='{_ps(cwd)}';"
            f"$s.IconLocation='{_ps(icon)},0';$s.Description='SwimTunes';$s.Save();"
            "Write-Output (Join-Path $d 'SwimTunes.lnk')"
        )
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or "Could not create the shortcut")
        return r.stdout.strip()
    if sys.platform == "darwin":
        if tools.is_frozen():
            raise RuntimeError("On a Mac, drag SwimTunes from Applications to your Dock.")
        path = Path.home() / "Desktop" / "SwimTunes.command"
        path.write_text(f"#!/bin/sh\ncd '{cwd}'\nexec '{prog}' {args}\n")
        path.chmod(0o755)
        return str(path)
    desktop = Path.home() / "Desktop"
    entry = (f"[Desktop Entry]\nType=Application\nName=SwimTunes\nExec={prog} {args}\nPath={cwd}\n"
             f"Icon={STATIC / 'icon.png'}\nTerminal=false\n")
    apps = Path.home() / ".local" / "share" / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    (apps / "swimtunes.desktop").write_text(entry)
    if desktop.is_dir():
        (desktop / "swimtunes.desktop").write_text(entry)
        (desktop / "swimtunes.desktop").chmod(0o755)
        return str(desktop / "swimtunes.desktop")
    return str(apps / "swimtunes.desktop")


def _ps(s: str) -> str:
    return s.replace("'", "''")
