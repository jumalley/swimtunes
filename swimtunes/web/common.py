"""Helpers shared by the dashboard's route modules."""

from __future__ import annotations

import os
import subprocess
import sys

from .. import updater
from ..i18n import msg


def _reveal(path: str) -> None:
    """Open a folder in Explorer / Finder."""
    if os.name == "nt":
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def _newer(latest: str, current: str) -> bool:
    a, b = updater._tag_tuple(latest), updater._tag_tuple(current)
    return bool(a and b and a > b)




def _friendly_link_error(e: Exception, url: str) -> Exception:
    """Turn 'playlist does not exist / is private' into advice to connect an account."""
    text = str(e)
    low = text.lower()
    if "spotify" in url and ("private" in low or "connect your spotify" in low):
        return RuntimeError(msg("err.sp_private", "This Spotify playlist is private. Connect your Spotify account in Settings, then try again."))
    if "youtube" in url or "youtu.be" in url:
        if "private" in low or "does not exist" in low or "sign in" in low or "unavailable" in low and "playlist" in low:
            from .. import accounts as acc
            if not acc.status()["youtube"]["connected"]:
                return RuntimeError(msg("err.yt_private", "YouTube won't show this playlist: it is private or doesn't exist. "
                                        "If it's yours, connect your YouTube account in Settings, then try again."))
            return RuntimeError(msg("err.yt_private_connected", "YouTube won't show this playlist, even with your account. "
                                    "Check the link, and that you're signed in to the right account in your browser."))
    return e
