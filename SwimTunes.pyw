"""SwimTunes launcher: double-click this file to start SwimTunes.

Needs Python 3.10 or newer (https://www.python.org/downloads/). Nothing else:
the first time, this sets SwimTunes up in its own folder (about a minute);
after that, each start checks for a new version, installs it, and opens
SwimTunes in your web browser.

Why a Python file and not an .exe? Windows' Smart App Control blocks programs
that aren't signed by a known publisher. Python is signed, so SwimTunes runs
through it. Your music stays in Music/SwimTunes, as always.

Files: Windows %LOCALAPPDATA%\\SwimTunes, Mac ~/Library/Application Support/SwimTunes,
Linux ~/.local/share/swimtunes (env, version, launcher.log, swimtunes.log).
"""

import json
import locale
import os
import subprocess
import sys
import threading
import time
import urllib.request

REPO = "jumalley/swimtunes"
PORT = 8765
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

if sys.platform == "win32":
    HOME = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "SwimTunes")
elif sys.platform == "darwin":
    HOME = os.path.expanduser("~/Library/Application Support/SwimTunes")
else:
    HOME = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "swimtunes")
ENV = os.path.join(HOME, "env")
VERSION_FILE = os.path.join(HOME, "version.txt")
LOG = os.path.join(HOME, "launcher.log")
APP_LOG = os.path.join(HOME, "swimtunes.log")

TEXTS = {
    "en": {"title": "SwimTunes", "setup": "Setting up SwimTunes (first time only, about a minute)…",
           "update": "Updating SwimTunes to {v}…", "start": "Starting SwimTunes…", "check": "Checking for updates…",
           "old_python": "SwimTunes needs Python 3.10 or newer. You have {v}.\n\nInstall the latest Python from python.org, then double-click SwimTunes again.",
           "offline": "SwimTunes needs the internet the first time, to set itself up. Check your connection and try again.",
           "failed": "SwimTunes could not start.\n\n{err}\n\nDetails are in:\n{log}"},
    "fr": {"title": "SwimTunes", "setup": "Installation de SwimTunes (la première fois seulement, environ une minute)…",
           "update": "Mise à jour de SwimTunes vers {v}…", "start": "Démarrage de SwimTunes…", "check": "Recherche de mises à jour…",
           "old_python": "SwimTunes a besoin de Python 3.10 ou plus récent. Vous avez {v}.\n\nInstallez la dernière version de Python sur python.org, puis double-cliquez à nouveau sur SwimTunes.",
           "offline": "SwimTunes a besoin d'internet la première fois pour s'installer. Vérifiez votre connexion et réessayez.",
           "failed": "SwimTunes n'a pas pu démarrer.\n\n{err}\n\nLes détails sont dans :\n{log}"},
    "es": {"title": "SwimTunes", "setup": "Preparando SwimTunes (solo la primera vez, un minuto aprox.)…",
           "update": "Actualizando SwimTunes a {v}…", "start": "Iniciando SwimTunes…", "check": "Buscando actualizaciones…",
           "old_python": "SwimTunes necesita Python 3.10 o más reciente. Tienes {v}.\n\nInstala el último Python desde python.org y vuelve a hacer doble clic en SwimTunes.",
           "offline": "SwimTunes necesita internet la primera vez para prepararse. Revisa tu conexión e inténtalo de nuevo.",
           "failed": "SwimTunes no pudo iniciarse.\n\n{err}\n\nLos detalles están en:\n{log}"},
    "de": {"title": "SwimTunes", "setup": "SwimTunes wird eingerichtet (nur beim ersten Mal, etwa eine Minute)…",
           "update": "SwimTunes wird auf {v} aktualisiert…", "start": "SwimTunes startet…", "check": "Suche nach Updates…",
           "old_python": "SwimTunes braucht Python 3.10 oder neuer. Du hast {v}.\n\nInstalliere das neueste Python von python.org und doppelklicke dann erneut auf SwimTunes.",
           "offline": "SwimTunes braucht beim ersten Mal Internet, um sich einzurichten. Prüfe die Verbindung und versuche es erneut.",
           "failed": "SwimTunes konnte nicht starten.\n\n{err}\n\nDetails stehen in:\n{log}"},
    "it": {"title": "SwimTunes", "setup": "Preparazione di SwimTunes (solo la prima volta, circa un minuto)…",
           "update": "Aggiornamento di SwimTunes a {v}…", "start": "Avvio di SwimTunes…", "check": "Controllo aggiornamenti…",
           "old_python": "SwimTunes richiede Python 3.10 o successivo. Hai {v}.\n\nInstalla l'ultimo Python da python.org, poi fai di nuovo doppio clic su SwimTunes.",
           "offline": "SwimTunes ha bisogno di internet la prima volta per prepararsi. Controlla la connessione e riprova.",
           "failed": "SwimTunes non è riuscito ad avviarsi.\n\n{err}\n\nI dettagli sono in:\n{log}"},
    "pt": {"title": "SwimTunes", "setup": "Preparando o SwimTunes (só na primeira vez, cerca de um minuto)…",
           "update": "Atualizando o SwimTunes para {v}…", "start": "Iniciando o SwimTunes…", "check": "Procurando atualizações…",
           "old_python": "O SwimTunes precisa do Python 3.10 ou mais novo. Você tem {v}.\n\nInstale o Python mais recente em python.org e clique duas vezes no SwimTunes de novo.",
           "offline": "O SwimTunes precisa de internet na primeira vez para se preparar. Verifique a conexão e tente de novo.",
           "failed": "O SwimTunes não conseguiu iniciar.\n\n{err}\n\nOs detalhes estão em:\n{log}"},
}


def _lang():
    try:
        if sys.platform == "win32":
            import ctypes
            code = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), "")
        else:
            code = locale.getlocale()[0] or os.environ.get("LANG", "")
    except Exception:
        code = ""
    return (code or "en")[:2].lower() if (code or "en")[:2].lower() in TEXTS else "en"


T = TEXTS[_lang()]


def log(line):
    os.makedirs(HOME, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(line).rstrip() + "\n")


# ---------- a small status window (works without it too) ----------
class Window:
    def __init__(self):
        self.root = self.label = None
        try:
            import tkinter as tk
            from tkinter import ttk
            self.root = tk.Tk()
            self.root.title(T["title"])
            self.root.resizable(False, False)
            frame = ttk.Frame(self.root, padding=18)
            frame.pack()
            self.label = ttk.Label(frame, text=T["check"], width=58)
            self.label.pack(anchor="w")
            bar = ttk.Progressbar(frame, mode="indeterminate", length=420)
            bar.pack(pady=(12, 0))
            bar.start(12)
            self.root.update_idletasks()
            w, h = self.root.winfo_width(), self.root.winfo_height()
            self.root.geometry(f"+{(self.root.winfo_screenwidth() - w) // 2}+{(self.root.winfo_screenheight() - h) // 3}")
            self.root.withdraw()          # only shown when something takes a while
        except Exception:
            self.root = None

    def say(self, text, show=True):
        log(text)
        if self.root is not None:
            self.root.after(0, lambda: (self.label.config(text=text), show and self.root.deiconify()))

    def run(self, work):
        """Run work() in the background while the window stays responsive."""
        result = {}

        def target():
            try:
                result["ok"] = work()
            except BaseException as e:   # noqa: BLE001 - shown to the user below
                result["error"] = e
            if self.root is not None:
                self.root.after(0, self.root.destroy)
        t = threading.Thread(target=target, daemon=True)
        t.start()
        if self.root is not None:
            self.root.mainloop()
        t.join()
        if "error" in result:
            raise result["error"]
        return result.get("ok")


def message(text, error=True):
    log(text)
    try:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, "SwimTunes", 0x10 if error else 0x40)
            return
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        (messagebox.showerror if error else messagebox.showinfo)("SwimTunes", text)
        r.destroy()
    except Exception:
        print(text)


# ---------- installing / updating ----------
def env_python(gui=False):
    if sys.platform == "win32":
        return os.path.join(ENV, "Scripts", "pythonw.exe" if gui else "python.exe")
    return os.path.join(ENV, "bin", "python")


def installed_version():
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            v = f.read().strip()
        return v if v and os.path.exists(env_python()) else None
    except OSError:
        return None


def latest_version():
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                     headers={"User-Agent": "SwimTunes-launcher", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=6) as r:
            return json.load(r).get("tag_name")
    except Exception as e:
        log(f"update check failed: {e}")
        return None


def _tag(v):
    try:
        return tuple(int(x) for x in (v or "").lstrip("v").split("."))
    except ValueError:
        return ()


def run(cmd):
    log("$ " + " ".join(cmd))
    with open(LOG, "a", encoding="utf-8") as out:
        r = subprocess.run(cmd, stdout=out, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(f"{os.path.basename(cmd[0])} {' '.join(cmd[1:3])} failed (code {r.returncode})")


def install(version):
    if not os.path.exists(env_python()):
        run([_base_python(), "-m", "venv", ENV])
    run([env_python(), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--upgrade",
         f"https://github.com/{REPO}/archive/refs/tags/{version}.zip"])
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(version)


def _base_python():
    """python.exe next to the pythonw.exe running this file (pip needs the console one)."""
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        console = exe[:-len("pythonw.exe")] + "python.exe"
        if os.path.exists(console):
            return console
    return exe


def running():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/version", timeout=1.5) as r:
            return json.load(r).get("app") == "swimtunes"
    except Exception:
        return False


def start_app(version):
    env = dict(os.environ, SWIMTUNES_TAG=version, SWIMTUNES_LAUNCHER=os.path.abspath(__file__),
               SWIMTUNES_LAUNCHER_PYTHON=sys.executable, PYTHONIOENCODING="utf-8")
    out = open(APP_LOG, "a", encoding="utf-8")
    kw = {"creationflags": NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0)} if sys.platform == "win32" \
        else {"start_new_session": True}
    p = subprocess.Popen([env_python(gui=True), "-m", "swimtunes.app"], env=env, stdout=out, stderr=out,
                         stdin=subprocess.DEVNULL, close_fds=True, cwd=HOME, **kw)
    for _ in range(80):                    # wait until it answers (or stops with an error)
        if running():
            return
        if p.poll() is not None:
            raise RuntimeError(_last_lines(APP_LOG) or f"SwimTunes stopped (code {p.returncode})")
        time.sleep(0.25)


def _last_lines(path, n=6):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:]).strip()
    except OSError:
        return ""


def main():
    if sys.version_info < (3, 10):
        message(T["old_python"].format(v=sys.version.split()[0]))
        return 1
    os.makedirs(HOME, exist_ok=True)
    if running() and not os.environ.get("SWIMTUNES_NO_BROWSER"):   # already open: just show it
        import webbrowser
        webbrowser.open(f"http://localhost:{PORT}")
        return 0
    win = Window()

    def work():
        have = installed_version()
        latest = latest_version()
        if not have and not latest:
            raise RuntimeError(T["offline"])
        if latest and (not have or _tag(latest) > _tag(have)):
            win.say(T["update"].format(v=latest) if have else T["setup"])
            try:
                install(latest)
                have = latest
            except Exception:
                if not have:
                    raise
                log("update failed, starting the installed version")   # keep working with what we have
        win.say(T["start"], show=False)
        start_app(have)

    try:
        win.run(work)
    except Exception as e:
        message(T["failed"].format(err=str(e)[-800:], log=LOG))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
