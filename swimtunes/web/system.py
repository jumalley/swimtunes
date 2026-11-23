"""Version, updates, health, status bar, opening folders, quitting."""

from __future__ import annotations

import os
import sys
import threading

from flask import jsonify

from .. import TAG, __version__, device, tools, updater
from ..i18n import msg


def register(app, ctx):
    lib, err, body, new_job, busy = (ctx.lib, ctx.err, ctx.body, ctx.new_job, ctx.busy)

    # ---------- app: version, updates, health, quit ----------
    @app.get("/api/version")
    def version():
        u = app.config["UPDATE"] or {}
        return jsonify({"app": "swimtunes", "version": __version__, "tag": TAG,
                        "kind": updater.install_kind(), "update": u if u.get("available") else None,
                        "can_quit": app.config["SHUTDOWN"] is not None, "updating": app.config.get("AUTO_UPDATE")})

    @app.get("/api/update")
    def update_check():
        app.config["UPDATE"] = updater.check()
        return jsonify(app.config["UPDATE"])

    @app.post("/api/update/apply")
    def update_apply():
        if app.config["SHUTDOWN"] is None:
            return err(msg("err.cant_update_here", "Start SwimTunes with the app or `swimtunes ui` to update from here"))
        if busy():
            return err(msg("err.busy_update", "Wait until the current download or copy has finished"))

        return jsonify({"id": run_update()})

    def run_update(auto=False):
        def fn(log, progress, job):
            u = app.config["UPDATE"] or {}
            job["current"] = u.get("latest")
            cmd = updater.apply(log)
            log(msg("log.upd_restart", "Restarting SwimTunes… this page reloads by itself."))
            app.config["RESTART"] = cmd
            threading.Timer(1.0, app.config["SHUTDOWN"]).start()
            return {"restart": True, "auto": auto, "latest": u.get("latest")}
        return new_job("update", fn)

    health_cache: dict = {}

    ctx.run_update = run_update

    @app.get("/api/status")
    def status():
        """Everything the status bar at the bottom of the page shows."""
        if "ok" not in health_cache:
            health_cache["ok"] = all(r["ok"] for r in tools.health())
        cfg = lib.config()
        dev = None
        path = cfg.get("device_path") or ""
        if path:
            drive = next((d for d in device.find_drives() if d["path"] == path), None)
            if drive:   # the same detection as the Earphones tab
                dev = {"path": path, "label": drive["label"], "free": drive["free"], "total": drive["total"]}
            elif os.path.isdir(path):
                try:
                    import shutil as _sh
                    u = _sh.disk_usage(path)
                    dev = {"path": path, "label": os.path.basename(path.rstrip("\\/")) or path, "free": u.free, "total": u.total}
                except OSError:
                    dev = None
        u = app.config["UPDATE"] or {}
        return jsonify({"ok": health_cache["ok"], "version": __version__, "tag": TAG, "kind": updater.install_kind(),
                        "update": u.get("latest") if u.get("available") else None, "device": dev,
                        "library": str(lib.root), "language": cfg.get("language", "auto")})

    @app.get("/api/health")
    def health():
        return jsonify({"tools": tools.health(), "kind": updater.install_kind(), "version": __version__, "tag": TAG,
                        "library": str(lib.root), "python": sys.version.split()[0], "platform": sys.platform})

    @app.post("/api/shortcut")
    def shortcut():
        from ..shortcut import create_desktop_shortcut
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
            return err(msg("err.quit_terminal", "Stop SwimTunes from the terminal with Ctrl+C"))
        if busy() and not body().get("force"):
            return err(msg("err.busy_quit", "A download or sync is still running"))
        threading.Timer(0.3, app.config["SHUTDOWN"]).start()
        return jsonify({"ok": True})

