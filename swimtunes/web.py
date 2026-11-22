"""Local web dashboard: http://localhost:8765"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

from flask import Flask, jsonify, render_template, request

from . import REPO, TAG, __version__, device
from .downloader import Downloader
from .library import Library, LibraryError


def create_app(lib: Library, downloader_cls=Downloader) -> Flask:
    app = Flask(__name__)
    jobs: dict[str, dict] = {}
    job_lock = threading.Lock()   # one download/sync at a time
    app.config.update(JOBS=jobs, LAST_SEEN=time.time(), SHUTDOWN=None, LATEST=None)
    if TAG != "dev":
        threading.Thread(target=_check_latest, args=(app,), daemon=True).start()

    @app.before_request
    def _seen():
        app.config["LAST_SEEN"] = time.time()

    def err(msg, code=400):
        return jsonify({"error": msg}), code

    @app.errorhandler(LibraryError)
    def _lib_err(e):
        return err(str(e))

    def body():
        return request.get_json(silent=True) or {}

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/library")
    def library():
        return jsonify({**lib.stats(), "config": lib.config()})

    @app.get("/api/folders/<name>")
    def folder(name):
        return jsonify([t.to_dict(lib.root) for t in lib.tracks(name)])

    @app.post("/api/folders")
    def create_folder():
        p = lib.create_folder(body().get("name", ""))
        return jsonify({"name": p.name})

    @app.post("/api/folders/<name>/rename")
    def rename_folder(name):
        p = lib.rename_folder(name, body().get("name", ""))
        return jsonify({"name": p.name})

    @app.delete("/api/folders/<name>")
    def delete_folder(name):
        lib.delete_folder(name)
        return jsonify({"ok": True})

    @app.post("/api/tracks/move")
    def move_track():
        b = body()
        p = lib.move_track(b.get("path", ""), b.get("folder", ""))
        return jsonify({"path": p.relative_to(lib.root).as_posix()})

    @app.post("/api/tracks/delete")
    def delete_tracks():
        paths = body().get("paths", [])
        for p in paths:
            lib.delete_track(p)
        return jsonify({"deleted": len(paths)})

    @app.get("/api/duplicates")
    def duplicates():
        return jsonify([[t.to_dict(lib.root) for t in g] for g in lib.duplicates(request.args.get("scope"))])

    @app.post("/api/config")
    def config():
        return jsonify(lib.set_config(**body()))

    @app.post("/api/trash/empty")
    def empty_trash():
        return jsonify({"deleted": lib.empty_trash()})

    def start_job(kind, fn):
        jid = uuid.uuid4().hex[:8]
        job = {"id": jid, "kind": kind, "status": "queued", "log": [], "result": None, "started": time.time()}
        jobs[jid] = job

        def log(line):
            job["log"].append(line)

        def run():
            with job_lock:
                job["status"] = "running"
                try:
                    job["result"] = fn(log)
                    job["status"] = "done"
                except Exception as e:
                    log(f"ERROR: {e}")
                    job["status"] = "error"

        threading.Thread(target=run, daemon=True).start()
        return jsonify({"id": jid})

    @app.post("/api/download")
    def download():
        b = body()
        urls = [u.strip() for u in (b.get("urls") or b.get("url") or "").split() if u.strip()]
        folder = (b.get("folder") or "").strip() or None
        if not urls:
            return err("Paste at least one link")

        def fn(log):
            d = downloader_cls(lib, log=log, allow_dupes=bool(b.get("allow_dupes")))
            return [d.download(u, folder).to_dict() for u in urls]
        return start_job("download", fn)

    @app.post("/api/sync")
    def sync():
        b = body()
        path = (b.get("device") or lib.config()["device_path"]).strip()
        if not path:
            return err("Set the earphones' drive path first")
        lib.set_config(device_path=path)
        return start_job("sync", lambda log: device.sync(lib, path, mirror=bool(b.get("mirror")), log=log))

    @app.get("/api/jobs/<jid>")
    def job(jid):
        j = jobs.get(jid)
        return jsonify(j) if j else err("No such job", 404)

    @app.get("/api/version")
    def version():
        latest = app.config["LATEST"]
        return jsonify({"app": "swimtunes", "version": __version__, "tag": TAG,
                        "latest": latest if latest and _newer(latest, TAG) else None,
                        "download": f"https://github.com/{REPO}/releases/latest",
                        "can_quit": app.config["SHUTDOWN"] is not None})

    @app.post("/api/ping")
    def ping():
        return jsonify({"ok": True})

    @app.post("/api/quit")
    def quit_app():
        if app.config["SHUTDOWN"] is None:
            return err("Stop SwimTunes from the terminal with Ctrl+C")
        if any(j["status"] in ("queued", "running") for j in jobs.values()) and not body().get("force"):
            return err("A download or sync is still running")
        threading.Timer(0.3, app.config["SHUTDOWN"]).start()
        return jsonify({"ok": True})

    @app.get("/api/drives")
    def drives():
        return jsonify(device.find_drives())

    @app.post("/api/open")
    def open_folder():
        name = body().get("folder")
        path = lib.folder_path(name) if name else lib.root
        _reveal(str(path))
        return jsonify({"ok": True})

    @app.get("/api/jobs")
    def job_list():
        return jsonify(sorted(jobs.values(), key=lambda j: -j["started"])[:10])

    return app


def _reveal(path: str) -> None:
    """Open a folder in Explorer / Finder."""
    if os.name == "nt":
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def _tag_tuple(tag: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in tag.lstrip("v").split("."))
    except ValueError:
        return ()


def _newer(latest: str, current: str) -> bool:
    a, b = _tag_tuple(latest), _tag_tuple(current)
    return bool(a and b and a > b)


def _check_latest(app: Flask) -> None:
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "SwimTunes"})
        with urllib.request.urlopen(req, timeout=10) as r:
            app.config["LATEST"] = json.load(r).get("tag_name")
    except Exception:
        pass
