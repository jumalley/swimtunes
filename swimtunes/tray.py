"""A small SwimTunes icon next to the clock (Windows) or in the menu bar (Mac): Open / Quit.

Neither app has a window of its own (the Mac app has no Dock icon either), so this icon
shows that SwimTunes is running and lets people reopen or quit it. If the icon can't be
shown (missing library, unusual desktop), SwimTunes simply runs without it: the page has
a Quit button and the app closes itself when no page is open.

Windows: the icon runs in a background thread (start). Mac: it must own the main thread,
so app.serve() runs the server in a thread and the icon in the main one (create + run).
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


MENU = {   # the menu follows the dashboard's language
    "en": ("Open SwimTunes", "Quit SwimTunes"), "fr": ("Ouvrir SwimTunes", "Quitter SwimTunes"),
    "es": ("Abrir SwimTunes", "Salir de SwimTunes"), "de": ("SwimTunes öffnen", "SwimTunes beenden"),
    "it": ("Apri SwimTunes", "Esci da SwimTunes"), "pt": ("Abrir o SwimTunes", "Sair do SwimTunes"),
}


def menu_labels(language: str = "auto") -> tuple[str, str]:
    if language in MENU:
        return MENU[language]
    import locale
    try:
        code = (locale.getlocale()[0] or os.environ.get("LANG", "en"))[:2].lower()
    except Exception:
        code = "en"
    return MENU.get(code, MENU["en"])


def create(url: str, quit_app: Callable[[], None], language: str = "auto", title: str = "SwimTunes"):
    """The icon, not shown yet (call .run() on the main thread). None if it can't be made."""
    if sys.platform not in ("win32", "darwin") or os.environ.get("SWIMTUNES_NO_TRAY"):
        return None
    try:
        import pystray
        image = _icon_image()
    except Exception:
        return None
    open_label, quit_label = menu_labels(language)

    def on_open(icon, item):
        webbrowser.open(url)

    def on_quit(icon, item):
        icon.stop()
        quit_app()

    return pystray.Icon("SwimTunes", image, title, menu=pystray.Menu(
        pystray.MenuItem(open_label, on_open, default=True),   # default = double-click / left click
        pystray.MenuItem(quit_label, on_quit),
    ))


def start(url: str, quit_app: Callable[[], None], language: str = "auto", title: str = "SwimTunes"):
    """Windows: show the icon in a background thread. Returns it (it has .stop()), or None."""
    if sys.platform != "win32":
        return None
    icon = create(url, quit_app, language, title)
    if icon is not None:
        threading.Thread(target=_run, args=(icon,), daemon=True).start()
    return icon


def _run(icon) -> None:
    try:
        icon.run()
    except Exception:
        pass
