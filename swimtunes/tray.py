"""A small SwimTunes icon next to the clock (Windows), like Discord: Open / Quit.

The Windows app has no console window, so this icon shows that SwimTunes is
running and lets people quit it. If the icon can't be shown (missing library,
unusual desktop), SwimTunes simply runs without it: the page has a Quit button
and the app closes itself when no page is open.
"""

from __future__ import annotations

import os
import sys
import threading
import webbrowser
from typing import Callable


def _icon_image():
    from PIL import Image
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, "static", "icon.png"), os.path.join(here, "static", "favicon.png")):
        if os.path.exists(path):
            return Image.open(path)
    img = Image.new("RGB", (64, 64), (10, 126, 164))   # plain blue square
    return img


def start(url: str, quit_app: Callable[[], None], title: str = "SwimTunes"):
    """Show the icon in a background thread. Returns an object with .stop(), or None."""
    if sys.platform != "win32" or os.environ.get("SWIMTUNES_NO_TRAY"):
        return None
    try:
        import pystray
        image = _icon_image()
    except Exception:
        return None

    def on_open(icon, item):
        webbrowser.open(url)

    def on_quit(icon, item):
        icon.stop()
        quit_app()

    icon = pystray.Icon("SwimTunes", image, title, menu=pystray.Menu(
        pystray.MenuItem("Open SwimTunes", on_open, default=True),   # default = double-click / left click
        pystray.MenuItem("Quit SwimTunes", on_quit),
    ))
    threading.Thread(target=_run, args=(icon,), daemon=True).start()
    return icon


def _run(icon) -> None:
    try:
        icon.run()
    except Exception:
        pass
