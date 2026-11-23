"""The music folder: songs, folders, playlist pictures, duplicates, settings."""

from __future__ import annotations

import os
import time

from flask import jsonify, render_template, request

from .. import covers, device, tools
from ..i18n import msg
from ..library import LibraryError
from ..text import safe_name
from .common import _reveal


def register(app, ctx):
    lib, err, body = (ctx.lib, ctx.err, ctx.body)

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

    @app.get("/api/cover")
    def cover():
        """The album picture inside an MP3 (for the Songs list)."""
        from flask import Response

        from ..library import read_cover
        pic = read_cover(lib.resolve_track(request.args.get("path", "")))
        if not pic:
            return err(msg("err.no_cover", "No picture"), 404)
        return Response(pic[0], mimetype=pic[1], headers={"Cache-Control": "max-age=86400"})

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

    # ---------- folder pictures ----------
    @app.get("/api/folders/<name>/picture")
    def folder_picture(name):
        from flask import Response
        p = lib.folder_cover(name)
        if not p:
            return err(msg("err.no_cover", "No picture"), 404)
        # read it whole: a file still open for the browser can't be replaced or removed on Windows
        return Response(p.read_bytes(), mimetype="image/jpeg", headers={"Cache-Control": "no-cache"})

    @app.post("/api/folders/<name>/picture")
    def set_folder_picture(name):
        """A new picture for a folder: an uploaded file, a web picture, a song's picture or a mosaic."""
        import base64

        from ..library import read_cover
        ffmpeg = tools.ffmpeg_path()
        f = request.files.get("file")
        b = {} if f else body()
        try:
            if f:
                raw = f.read(15_000_000)
            elif b.get("song"):
                pic = read_cover(lib.resolve_track(b["song"]))
                if not pic:
                    return err(msg("err.no_cover", "No picture"), 404)
                raw = pic[0]
            elif str(b.get("data", "")).startswith("data:image/"):
                raw = base64.b64decode(b["data"].split(",", 1)[1])
            elif b.get("url"):
                raw = covers.download(b["url"])
            else:
                return err(msg("err.no_picture", "Choose a picture first"))
            if not ffmpeg:
                return err(msg("err.no_ffmpeg", "ffmpeg is missing"))
            jpeg = covers.square_jpeg(raw, ffmpeg, 600)
        except LibraryError:
            raise
        except Exception as e:
            return err(msg("err.bad_picture", "Couldn't use this picture: {error}", error=str(e)[:200]))
        lib.set_folder_cover(name, jpeg)
        return jsonify({"ok": True, "v": int(time.time())})

    @app.delete("/api/folders/<name>/picture")
    def delete_folder_picture(name):
        lib.set_folder_cover(name, None)
        return jsonify({"ok": True})

    @app.get("/api/folders/<name>/pictures")
    def folder_picture_ideas(name):
        if not lib.folder_path(name).is_dir():
            return err(msg("err.no_folder", "No such folder: {name}", name=name), 404)
        ideas = covers.suggest(lib.tracks(name), name, request.args.get("q") or None)
        ideas["songs"] = [p.relative_to(lib.root).as_posix() for p in ideas["songs"]]
        return jsonify(ideas)

    @app.get("/api/earphones/folders")
    def folders_on_earphones():
        """Per folder: is it the same on the plugged-in earphones? (empty when not plugged in)"""
        path = lib.config()["device_path"]
        drives = device.find_drives()
        if not path or not os.path.isdir(path):
            path = drives[0]["path"] if drives else ""
        if not path or not os.path.isdir(path):
            return jsonify({"path": None, "folders": {}})
        try:
            rows = device.compare(lib, path)["folders"]
        except OSError:
            return jsonify({"path": None, "folders": {}})
        return jsonify({"path": path, "folders": {r["name"]: r["status"] for r in rows}})

