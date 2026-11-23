"""The earphones: drives, compare, import, copy; battery and settings over Bluetooth."""

from __future__ import annotations

import os
import sys
import time

from flask import jsonify, request

from .. import device, shokz, tools
from ..i18n import msg


def register(app, ctx):
    lib, err, body, start_job, busy = (ctx.lib, ctx.err, ctx.body, ctx.start_job, ctx.busy)

    # ---------- earphones ----------
    @app.get("/api/drives")
    def drives():
        found = device.find_drives()
        saved = lib.config()["device_path"]   # a location typed in by hand earlier
        if saved and os.path.isdir(saved) and not any(os.path.normcase(d["path"]) == os.path.normcase(saved) for d in found):
            import shutil
            u = shutil.disk_usage(saved)
            found.append({"path": saved, "label": os.path.basename(saved.rstrip("/\\")) or saved, "total": u.total,
                          "free": u.free, "likely": True, "saved": True})
        return jsonify(found)

    @app.get("/api/device")
    def device_compare():
        path = (request.args.get("path") or lib.config()["device_path"] or "").strip()
        if not path:
            return err(msg("err.pick_earphones", "Pick your earphones first"))
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
            return err(msg("err.pick_import", "Choose the folders to import"))
        def fn(log, progress, job):
            def report(entry):
                job["recent"].append(entry)
                job["current"] = entry["file"]
            return device.import_from_device(
                lib, path, folders, ffmpeg=tools.ffmpeg_path(), log=log, scope=lib.config()["dupe_scope"],
                progress=progress, allow_dupes=bool(b.get("allow_dupes")), report=report,
                root_name=(b.get("root_name") or "From earphones").strip() or "From earphones")
        return start_job("import", fn)

    @app.post("/api/sync")
    def sync():
        b = body()
        path = (b.get("device") or lib.config()["device_path"]).strip()
        if not path:
            return err(msg("err.pick_earphones", "Pick your earphones first"))
        lib.set_config(device_path=path)
        def run(log, progress, job):
            summary = device.sync(lib, path, mirror=bool(b.get("mirror")), replace=bool(b.get("replace")),
                                  folders=b.get("folders") or None, log=log, progress=progress)
            synced = dict(lib.config().get("synced") or {})
            now = time.time()
            for name in summary["updated"] + summary["unchanged"]:
                synced[name] = now
            lib.set_config(synced={k: v for k, v in synced.items() if k in set(lib.folders())})
            return summary
        return start_job("sync", run)

    @app.post("/api/eject")
    def eject():
        path = (body().get("device") or lib.config()["device_path"]).strip()
        if not path:
            return err(msg("err.pick_earphones", "Pick your earphones first"))
        if busy():
            return err(msg("err.busy_eject", "Wait until the copy has finished"))
        r = device.eject(path)
        if not r["ok"]:
            return err(msg("err.eject", "Couldn't eject the earphones ({detail}). Close any window showing them, "
                           "then try again, or use Safely Remove / Eject in your computer.", detail=r["detail"] or "busy"))
        return jsonify(r)

    # ---------- earphones over Bluetooth (battery, firmware) ----------
    @app.get("/api/shokz")
    def shokz_status():
        return jsonify({"devices": shokz.paired_devices(), "can_connect": shokz.can_connect(),
                        "platform": sys.platform, "last": lib.config().get("shokz") or {}})

    @app.post("/api/shokz/read")
    def shokz_read():
        b = body()

        def run(log, progress, job):
            devs = shokz.paired_devices()
            dev = next((d for d in devs if d["address"] == b.get("address")), devs[0] if devs else None)
            if not dev:
                raise shokz.ShokzError(msg("err.shokz_none", "No Shokz earphones are paired with this computer. "
                                           "Pair them once in the computer's Bluetooth settings."))
            last = lib.config().get("shokz") or {}
            job["current"] = dev["name"]
            log(msg("log.shokz_connecting", "Connecting to {name} over Bluetooth…", name=dev["name"]))
            if sys.platform == "darwin" or not shokz.can_connect():
                info = {k: dev.get(k) for k in ("name", "address", "battery", "firmware") if dev.get(k) is not None}
                info["partial"] = True
            else:
                cached = last.get("channel") if last.get("address") == dev["address"] else None
                mode = b.get("mp3_mode") if b.get("mp3_mode") in shokz.MP3_MODES.values() else None
                kw = {"mp3_mode": mode} if mode else {}
                info = shokz.read_device(dev["address"], name=dev["name"], channel=dev.get("channel") or cached, **kw)
            info["time"] = time.time()
            info["play_hours"] = shokz.play_hours(dev["name"], "mp3")    # swims are done in MP3 mode
            lib.set_config(shokz=info)
            log(msg("log.shokz_done", "Read {name}", name=dev["name"]))
            return info
        return start_job("shokz", run, exclusive=False)

