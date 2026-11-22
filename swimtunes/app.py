"""Start the dashboard: what happens when you double-click SwimTunes."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

from . import TAG, __version__, tools
from .library import Library

DEFAULT_PORT = 8765
IDLE_MINUTES = 10  # the app closes itself this long after the last browser tab is closed


def _fix_stdio(lib: Library) -> None:
    """A Mac app has no terminal: write messages to a log file instead.
    On Windows, make sure odd characters in song titles can't crash printing."""
    if sys.stdout is None or sys.stderr is None:
        f = open(lib.root / ".swimtunes.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except Exception:
            pass


def _unquarantine() -> None:
    """macOS marks downloaded apps as 'quarantined', which can block the helper
    programs inside the app (ffmpeg, deno) even after the user allowed the app."""
    if sys.platform != "darwin" or not tools.is_frozen():
        return
    app_root = os.path.abspath(os.path.join(os.path.dirname(sys.executable), "..", ".."))
    if app_root.endswith(".app"):
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", app_root], capture_output=True)


def _running_swimtunes(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=1.5) as r:
            return json.load(r).get("app") == "swimtunes"
    except Exception:
        return False


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def serve(lib: Library, port: int = DEFAULT_PORT, open_browser: bool = True,
          idle_minutes: float | None = None) -> int:
    from werkzeug.serving import make_server
    from .web import create_app

    if _running_swimtunes(port):  # already open: just show it again
        if open_browser:
            webbrowser.open(f"http://localhost:{port}")
        print(f"SwimTunes is already running: http://localhost:{port}")
        return 0
    if not _port_free(port):
        for p in range(port + 1, port + 50):
            if _port_free(p):
                port = p
                break

    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)  # no request spam in the window
    app = create_app(lib)
    server = make_server("127.0.0.1", port, app, threaded=True)
    app.config["SHUTDOWN"] = lambda: threading.Thread(target=server.shutdown, daemon=True).start()
    app.config["RESTART"] = None  # set by the updater: command that starts the new version
    url = f"http://localhost:{port}"

    print("=" * 56)
    print(f"  SwimTunes {TAG if TAG != 'dev' else __version__} is running")
    print(f"  Dashboard: {url}")
    print(f"  Music folder: {lib.root}")
    print("  To stop: click Quit in the dashboard (or the icon next to the clock on Windows).")
    print("=" * 56, flush=True)

    tray = None
    if tools.is_frozen():   # the Windows app has no window: an icon next to the clock shows it's running
        from . import tray as tray_mod
        tray = tray_mod.start(url, app.config["SHUTDOWN"])
    if open_browser and not os.environ.pop("SWIMTUNES_NO_BROWSER", None):
        threading.Timer(0.8, webbrowser.open, [url]).start()
    if idle_minutes:
        threading.Thread(target=_idle_watch, args=(app, idle_minutes), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
    if tray is not None:
        try:
            tray.stop()
        except Exception:
            pass
    if app.config["RESTART"]:
        from .updater import restart
        print("Restarting with the new version…", flush=True)
        cmd = list(app.config["RESTART"])
        if "ui" in cmd:  # running from source: keep the same music folder and port
            i = cmd.index("ui")
            cmd[i:i] = ["--library", str(lib.root)]
            cmd += ["--port", str(port)]
        restart(cmd)
    return 0


def _idle_watch(app, minutes: float) -> None:
    while True:
        time.sleep(30)
        idle = time.time() - app.config["LAST_SEEN"]
        busy = any(j["status"] in ("queued", "running") for j in app.config["JOBS"].values())
        if idle > minutes * 60 and not busy:
            print("No dashboard open for a while: closing SwimTunes.", flush=True)
            app.config["SHUTDOWN"]()
            return


def main() -> int:
    """Entry point of the double-click app. With arguments, acts as the command line tool."""
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-psn"):  # -psn_* is added by old macOS
        _attach_terminal()
        from .cli import main as cli_main
        return cli_main()
    lib = Library()
    _fix_stdio(lib)
    _unquarantine()
    from .updater import cleanup_old_versions
    cleanup_old_versions()
    try:
        return serve(lib, idle_minutes=IDLE_MINUTES)
    except Exception as e:  # tell the user what went wrong: the Windows app has no console window
        print(f"\nSwimTunes could not start: {e}", flush=True)
        _error_box(f"SwimTunes could not start:\n\n{e}")
        return 1


def _attach_terminal() -> None:
    """The Windows app has no window of its own: when started from a terminal with
    arguments (e.g. `SwimTunes.exe doctor`), print into that terminal."""
    if os.name != "nt" or sys.stdout is not None:
        return
    try:
        import ctypes
        if ctypes.windll.kernel32.AttachConsole(-1):     # -1 = the terminal that started us
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
            sys.stderr = sys.stdout
    except Exception:
        pass


def _error_box(text: str) -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "SwimTunes", 0x10)   # 0x10 = error icon
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
