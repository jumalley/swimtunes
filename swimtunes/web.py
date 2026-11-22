"""Local web dashboard: http://localhost:8765"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid

from flask import Flask, jsonify, render_template, request

from . import TAG, __version__, device, sorter, tools, updater
from .downloader import Downloader
from .library import Library, LibraryError
from .meta import MetaLookup, country_name, tempo_label
from .text import safe_name


def create_app(lib: Library, downloader_cls=Downloader, meta_lookup=None) -> Flask:
    app = Flask(__name__)
    jobs: dict[str, dict] = {}
    job_lock = threading.Lock()   # one long task at a time
    app.config.update(JOBS=jobs, LAST_SEEN=time.time(), SHUTDOWN=None, RESTART=None, UPDATE=None)

    def lookup():
        return meta_lookup or MetaLookup(lib.root / ".swimtunes-meta.json")

    def _bg_update_check():
        app.config["UPDATE"] = updater.check()
    if not os.environ.get("SWIMTUNES_OFFLINE") and (TAG != "dev" or updater.install_kind() == "git"):
        threading.Thread(target=_bg_update_check, daemon=True).start()

    @app.before_request
    def _seen():
        app.config["LAST_SEEN"] = time.time()

    def err(msg, code=400):
        return jsonify({"error": msg}), code

    @app.errorhandler(LibraryError)
    def _lib_err(e):
        return err(str(e))

    @app.errorhandler(FileNotFoundError)
    def _fnf(e):
        return err(str(e))

    def body():
        return request.get_json(silent=True) or {}

    # ---------- jobs (long tasks shown with a live log) ----------
    def start_job(kind, fn):
        jid = uuid.uuid4().hex[:8]
        job = {"id": jid, "kind": kind, "status": "queued", "log": [], "result": None, "started": time.time(),
               "progress": None}
        jobs[jid] = job

        def log(line):
            job["log"].append(line)

        def progress(done, total):
            job["progress"] = [done, total]

        def run():
            with job_lock:
                job["status"] = "running"
                try:
                    job["result"] = fn(log, progress, job)
                    job["status"] = "done"
                except Exception as e:
                    log(f"ERROR: {e}")
                    job["status"] = "error"

        threading.Thread(target=run, daemon=True).start()
        return jsonify({"id": jid})

    @app.get("/api/jobs/<jid>")
    def job(jid):
        j = jobs.get(jid)
        return jsonify(j) if j else err("No such job", 404)

    def busy():
        return any(j["status"] in ("queued", "running") for j in jobs.values())

    # ---------- library ----------
    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/library")
    def library():
        return jsonify({**lib.stats(), "config": lib.config()})

    @app.get("/api/folders/<name>")
    def folder(name):
        return jsonify([t.to_dict(lib.root) for t in lib.tracks(name)])

    @app.get("/api/tracks")
    def all_tracks():
        return jsonify([t.to_dict(lib.root) for t in lib.tracks()])

    @app.post("/api/folders")
    def create_folder():
        p = lib.create_folder(body().get("name", ""))
        return jsonify({"name": p.name})

    @app.post("/api/folders/<name>/rename")
    def rename_folder(name):
        p = lib.rename_folder(name, body().get("name", ""))
        rules = lib.config()["rules"]  # keep rules pointing at the renamed folder
        for r in rules:
            if r.get("folder") == name:
                r["folder"] = p.name
        lib.set_config(rules=rules)
        return jsonify({"name": p.name})

    @app.delete("/api/folders/<name>")
    def delete_folder(name):
        lib.delete_folder(name)
        return jsonify({"ok": True})

    @app.post("/api/tracks/move")
    def move_tracks():
        b = body()
        moves = b.get("moves") or [{"path": b.get("path", ""), "folder": b.get("folder", "")}]
        moved = []
        for m in moves:
            dest = safe_name(m["folder"])
            if not lib.folder_path(dest).is_dir():
                lib.create_folder(dest)
            moved.append(lib.move_track(m["path"], dest).relative_to(lib.root).as_posix())
        return jsonify({"moved": moved, "path": moved[0] if moved else None})

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

    @app.post("/api/open")
    def open_folder():
        name = body().get("folder")
        path = lib.folder_path(name) if name else lib.root
        _reveal(str(path))
        return jsonify({"ok": True})

    # ---------- adding music: preview, sort, download ----------
    @app.post("/api/preview")
    def preview():
        b = body()
        urls = [u.strip() for u in (b.get("urls") or "").split() if u.strip()]
        if not urls:
            return err("Paste at least one link")

        def fn(log, progress, job):
            d = downloader_cls(lib, log=log, allow_dupes=bool(b.get("allow_dupes")))
            items, names = [], []
            for u in urls:
                log(f"Reading {u}")
                data = d.resolve(u)
                names.append(data["name"])
                items += data["items"]
                log(f"Found {len(data['items'])} song(s) in '{data['name']}'")
            d.mark_existing(items)  # also catches songs listed in two of the links
            for i, it in enumerate(items):
                it["id"] = i
            job["items"] = items
            new = [it for it in items if not it["existing"]]
            log(f"{len(new)} new, {len(items) - len(new)} already in SwimTunes. Looking up genres…")
            look = lookup()
            for i, it in enumerate(new, 1):
                try:
                    it["meta"] = look.lookup(it["artist"], it["title"])
                    it["meta"]["country_name"] = country_name(it["meta"].get("country", ""))
                except Exception as e:
                    it["meta"] = None
                    if i == 1:
                        log(f"Genre lookup unavailable ({e}); you can still download.")
                progress(i, len(new))
            look.save()
            log("Ready. Check the list, choose how to sort, then download.")
            return {"name": names[0] if len(names) == 1 else "Downloads", "items": items}
        return start_job("preview", fn)

    @app.post("/api/sort")
    def sort():
        """Where each song would go for a given way of sorting."""
        b = body()
        mode, folder = b.get("mode", "one"), (b.get("folder") or "").strip()
        rules = b.get("rules") if b.get("rules") is not None else lib.config()["rules"]
        out = [sorter.destination(mode, it.get("artist", ""), it.get("meta"), folder=folder, rules=rules)
               for it in b.get("items", [])]
        if b.get("remember"):
            lib.set_config(sort_mode=mode)
        return jsonify({"folders": out})

    @app.post("/api/download")
    def download():
        b = body()
        items = b.get("items")
        if items is None:  # simple form: links + one folder
            urls = [u.strip() for u in (b.get("urls") or b.get("url") or "").split() if u.strip()]
            folder = (b.get("folder") or "").strip() or None
            if not urls:
                return err("Paste at least one link")

            def simple(log, progress, job):
                d = downloader_cls(lib, log=log, allow_dupes=bool(b.get("allow_dupes")))
                return [d.download(u, folder).to_dict() for u in urls]
            return start_job("download", simple)
        if not items:
            return err("Tick at least one song")
        for it in items:
            if not (it.get("folder") or "").strip() and not it.get("tempo_folder"):
                return err(f"Choose a folder for {it.get('title')}")

        def fn(log, progress, job):
            d = downloader_cls(lib, log=log, allow_dupes=bool(b.get("allow_dupes")))
            count = [0]

            def after(_):
                count[0] += 1
                progress(count[0], len(items))
            return d.download_items(items, after_each=after).to_dict()
        return start_job("download", fn)

    # ---------- sorting rules & organizing the library ----------
    @app.get("/api/rules")
    def get_rules():
        return jsonify({"rules": lib.config()["rules"], "modes": sorter.MODES})

    @app.post("/api/rules")
    def set_rules():
        rules = []
        for r in body().get("rules", []):
            name = safe_name((r.get("folder") or "").strip()) if (r.get("folder") or "").strip() else ""
            kws = [k.strip() for k in (r.get("keywords") or []) if k and k.strip()]
            if name and kws:
                rules.append({"folder": name, "keywords": kws})
        lib.set_config(rules=rules)
        return jsonify({"rules": rules})

    @app.post("/api/rules/suggest")
    def suggest_rules():
        def fn(log, progress, job):
            tracks = lib.tracks()
            sorter.fill_meta(lib, tracks, log=log, progress=progress)
            rules = sorter.suggest_rules(tracks)
            log(f"Found {len(rules)} rule(s) from your folders." if rules else
                "Couldn't find anything your folders have in common yet.")
            return {"rules": rules}
        return start_job("suggest", fn)

    @app.post("/api/organize/analyze")
    def organize_analyze():
        folder = body().get("folder") or None

        def fn(log, progress, job):
            tracks = lib.tracks(folder)
            sorter.fill_meta(lib, tracks, log=log, progress=progress)
            log("Done. Choose how to sort and check where each song would go.")
            return {"tracks": [t.to_dict(lib.root) for t in tracks]}
        return start_job("analyze", fn)

    # ---------- earphones ----------
    @app.get("/api/drives")
    def drives():
        return jsonify(device.find_drives())

    @app.get("/api/device")
    def device_compare():
        path = (request.args.get("path") or lib.config()["device_path"] or "").strip()
        if not path:
            return err("Pick your earphones first")
        info = device.compare(lib, path)
        updates = {"device_path": path}
        if info["capacity_gb"] != lib.config()["capacity_gb"]:
            updates["capacity_gb"] = info["capacity_gb"]  # learn the earphones' size
        lib.set_config(**updates)
        return jsonify(info)

    @app.post("/api/device/import")
    def device_import():
        b = body()
        path, folders = (b.get("device") or "").strip(), b.get("folders") or []
        if not path or not folders:
            return err("Choose the folders to import")
        return start_job("import", lambda log, progress, job: device.import_from_device(
            lib, path, folders, ffmpeg=tools.ffmpeg_path(), log=log, scope=lib.config()["dupe_scope"]))

    @app.post("/api/sync")
    def sync():
        b = body()
        path = (b.get("device") or lib.config()["device_path"]).strip()
        if not path:
            return err("Pick your earphones first")
        lib.set_config(device_path=path)
        return start_job("sync", lambda log, progress, job: device.sync(
            lib, path, mirror=bool(b.get("mirror")), replace=bool(b.get("replace")),
            folders=b.get("folders") or None, log=log))

    # ---------- app: version, updates, health, quit ----------
    @app.get("/api/version")
    def version():
        u = app.config["UPDATE"] or {}
        return jsonify({"app": "swimtunes", "version": __version__, "tag": TAG,
                        "kind": updater.install_kind(), "update": u if u.get("available") else None,
                        "can_quit": app.config["SHUTDOWN"] is not None})

    @app.get("/api/update")
    def update_check():
        app.config["UPDATE"] = updater.check()
        return jsonify(app.config["UPDATE"])

    @app.post("/api/update/apply")
    def update_apply():
        if app.config["SHUTDOWN"] is None:
            return err("Start SwimTunes with the app or `swimtunes ui` to update from here")
        if busy():
            return err("Wait until the current download or copy has finished")

        def fn(log, progress, job):
            cmd = updater.apply(log)
            log("Restarting SwimTunes… this page reloads by itself.")
            app.config["RESTART"] = cmd
            threading.Timer(1.0, app.config["SHUTDOWN"]).start()
            return {"restart": True}
        return start_job("update", fn)

    @app.get("/api/health")
    def health():
        return jsonify({"tools": tools.health(), "kind": updater.install_kind(), "version": __version__, "tag": TAG,
                        "library": str(lib.root), "python": sys.version.split()[0], "platform": sys.platform})

    @app.post("/api/shortcut")
    def shortcut():
        from .shortcut import create_desktop_shortcut
        try:
            return jsonify({"path": create_desktop_shortcut()})
        except Exception as e:
            return err(str(e))

    @app.post("/api/ping")
    def ping():
        return jsonify({"ok": True})

    @app.post("/api/quit")
    def quit_app():
        if app.config["SHUTDOWN"] is None:
            return err("Stop SwimTunes from the terminal with Ctrl+C")
        if busy() and not body().get("force"):
            return err("A download or sync is still running")
        threading.Timer(0.3, app.config["SHUTDOWN"]).start()
        return jsonify({"ok": True})

    return app


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


__all__ = ["create_app", "country_name", "tempo_label"]
