"""Local web dashboard: http://localhost:8765

One module per area registers its routes; they share the library, the background
jobs and a few helpers through `ctx`."""

from __future__ import annotations

import contextlib
import os
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from flask import Flask, jsonify, request

from .. import TAG, updater
from ..downloader import Downloader
from ..i18n import as_entry, msg
from ..library import Library, LibraryError
from ..meta import MetaLookup, country_name, tempo_label
from . import accounts_routes, adding, earphones, library_routes, system
from .common import _newer  # noqa: F401  (used by tests)

def create_app(lib: Library, downloader_cls=Downloader, meta_lookup=None) -> Flask:
    pkg = Path(__file__).resolve().parents[1]        # templates/ and static/ are in swimtunes/
    app = Flask(__name__, template_folder=str(pkg / "templates"), static_folder=str(pkg / "static"))
    jobs: dict[str, dict] = {}
    job_lock = threading.Lock()   # one long task at a time
    app.config.update(JOBS=jobs, LAST_SEEN=time.time(), SHUTDOWN=None, RESTART=None, UPDATE=None)

    def lookup():
        return meta_lookup or MetaLookup(lib.root / ".swimtunes-meta.json")

    def _bg_update_check():
        app.config["UPDATE"] = u = updater.check()
        maybe_auto_update(u)

    def maybe_auto_update(u, wait=15.0):
        """Like Discord: when SwimTunes starts and a new version exists, install it and restart."""
        cfg = lib.config()
        if not (u.get("available") and cfg.get("auto_update") and u.get("kind") in ("app", "git")):
            return None
        end = time.time() + wait            # the app sets SHUTDOWN just after creating the dashboard
        while app.config["SHUTDOWN"] is None and time.time() < end:
            time.sleep(0.1)
        tried = cfg.get("auto_update_tried") or {}
        if app.config["SHUTDOWN"] is None or busy() or (
                tried.get("latest") == u.get("latest") and time.time() - tried.get("time", 0) < 6 * 3600):
            return None                     # already tried this one recently: don't loop, the banner stays
        lib.set_config(auto_update_tried={"latest": u.get("latest"), "time": time.time()})
        app.config["AUTO_UPDATE"] = jid = ctx.run_update(auto=True)   # defined by system.py
        return jid
    app.config["MAYBE_AUTO_UPDATE"] = maybe_auto_update
    if not os.environ.get("SWIMTUNES_OFFLINE") and (TAG != "dev" or updater.install_kind() == "git"):
        threading.Thread(target=_bg_update_check, daemon=True).start()

    @app.before_request
    def _seen():
        app.config["LAST_SEEN"] = time.time()

    def err(message, code=400):
        """Error for the dashboard; `message` may be a translatable Msg."""
        e = as_entry(message)
        return jsonify({"error": e["t"], "key": e.get("k"), "params": e.get("p")}), code

    @app.errorhandler(LibraryError)
    def _lib_err(e):
        return err(e.args[0] if e.args else str(e))

    @app.errorhandler(FileNotFoundError)
    def _fnf(e):
        return err(e.args[0] if e.args else str(e))

    def body():
        return request.get_json(silent=True) or {}

    # ---------- jobs (long tasks shown with a live log) ----------
    _no_lock = contextlib.nullcontext()

    def start_job(kind, fn, exclusive=True):
        return jsonify({"id": new_job(kind, fn, exclusive)})

    def new_job(kind, fn, exclusive=True):
        """Run fn in the background; returns the job id (works outside a web request too)."""
        jid = uuid.uuid4().hex[:8]
        job = {"id": jid, "kind": kind, "status": "queued", "log": [], "result": None, "started": time.time(),
               "progress": None, "current": None, "recent": [], "cancel": False}
        jobs[jid] = job

        def log(line):
            job["log"].append(as_entry(line))

        def progress(done, total):
            job["progress"] = [done, total]

        def run():
            with job_lock if exclusive else _no_lock:
                job["status"] = "running"
                try:
                    job["result"] = fn(log, progress, job)
                    job["status"] = "done"
                except Exception as e:
                    entry = as_entry(e)
                    entry["error"] = True
                    job["log"].append(entry)
                    job["status"] = "error"
                job["current"] = None

        threading.Thread(target=run, daemon=True).start()
        return jid

    @app.get("/api/jobs/<jid>")
    def job(jid):
        j = jobs.get(jid)
        return jsonify(j) if j else err(msg("err.no_job", "No such job"), 404)

    @app.post("/api/jobs/<jid>/cancel")
    def cancel_job(jid):
        j = jobs.get(jid)
        if not j:
            return err(msg("err.no_job", "No such job"), 404)
        j["cancel"] = True
        return jsonify({"ok": True})

    @app.get("/api/jobs/active")
    def active_jobs():
        """For the status bar: what SwimTunes is busy with right now."""
        return jsonify([{k: j[k] for k in ("id", "kind", "status", "progress", "current")} | {"last": (j["log"] or [None])[-1]}
                        for j in sorted(jobs.values(), key=lambda j: j["started"])
                        if j["status"] in ("queued", "running")])

    def busy():
        return any(j["status"] in ("queued", "running") for j in jobs.values())


    ctx = SimpleNamespace(lib=lib, jobs=jobs, job_lock=job_lock, err=err, body=body, start_job=start_job, new_job=new_job,
                          busy=busy, lookup=lookup, downloader_cls=downloader_cls)
    for area in (library_routes, adding, earphones, accounts_routes, system):
        area.register(app, ctx)
    return app


__all__ = ["create_app", "country_name", "tempo_label"]   # re-exported for older imports
