"""Local web dashboard: http://localhost:8765"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid

from flask import Flask, jsonify, render_template, request

from . import TAG, __version__, accounts, device, i18n, sorter, tools, updater
from .i18n import as_entry, msg
from .downloader import Downloader
from .library import Library, LibraryError
from .meta import MetaLookup, country_name, tempo_label
from .text import safe_name, same_song, song_key


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
    def start_job(kind, fn):
        jid = uuid.uuid4().hex[:8]
        job = {"id": jid, "kind": kind, "status": "queued", "log": [], "result": None, "started": time.time(),
               "progress": None, "current": None, "recent": [], "cancel": False}
        jobs[jid] = job

        def log(line):
            job["log"].append(as_entry(line))

        def progress(done, total):
            job["progress"] = [done, total]

        def run():
            with job_lock:
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
        return jsonify({"id": jid})

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
        from .library import read_cover
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

    # ---------- adding music: preview, sort, download ----------
    @app.post("/api/preview")
    def preview():
        b = body()
        urls = [u.strip() for u in (b.get("urls") or "").split() if u.strip()]
        if not urls:
            return err(msg("err.no_links", "Paste at least one link"))

        def fn(log, progress, job):
            d = downloader_cls(lib, log=log, allow_dupes=bool(b.get("allow_dupes")))
            items, names = [], []
            for u in urls:
                job["current"] = u
                log(msg("log.pv_reading", "Reading {url}", url=u))
                try:
                    data = d.resolve(u)
                except Exception as e:
                    raise _friendly_link_error(e, u) from None
                names.append(data["name"])
                items += data["items"]
                log(msg("log.pv_found", "Found {n} song(s) in '{name}'", n=len(data["items"]), name=data["name"]))
            d.mark_existing(items)  # also catches songs listed in two of the links
            new = [it for it in items if not it["existing"]]
            for i, it in enumerate(items):
                it["id"] = i
                it["state"] = "waiting" if not it["existing"] else "have"
            job["items"] = items          # the dashboard shows the list right away and fills it in live
            job["name"] = names[0] if len(names) == 1 else None
            log(msg("log.pv_new", "{new} new, {have} already in SwimTunes. Looking up genres…",
                    new=len(new), have=len(items) - len(new)))
            look = lookup()
            failures = 0
            for i, it in enumerate(new, 1):
                if job["cancel"]:
                    for rest in new[i - 1:]:
                        rest["state"] = "skipped"
                    log(msg("log.pv_stopped", "Stopped looking up. {n} song(s) have no genre info.", n=len(new) - i + 1))
                    break
                job["current"] = f"{it['artist']} - {it['title']}" if it["artist"] else it["title"]
                job["current_id"] = it["id"]
                it["state"] = "looking"
                renamed = False
                if it.get("needs_details") and it.get("video_id"):
                    it["state"] = "reading"          # the playlist said too little: read the video's page
                    before = (it["artist"], it["title"])
                    d.add_details(it)
                    renamed = (it["artist"], it["title"]) != before
                    job["current"] = f"{it['artist']} - {it['title']}" if it["artist"] else it["title"]
                    if it.get("unavailable"):
                        it["state"] = "unavailable"
                        job["recent"].append(it["id"])
                        progress(i, len(new))
                        continue
                    it["state"] = "looking"
                if it.get("unknown"):               # still only a video code: nothing to look up
                    it["meta"], it["state"] = None, "unknown"
                    job["recent"].append(it["id"])
                    progress(i, len(new))
                    continue
                try:
                    it["meta"] = look.lookup(it["artist"], it["title"],
                                             {"reliable": it.get("reliable", True), "alt": it.get("alt"), "text": it.get("text", "")})
                    if it.get("cover") and not it["meta"].get("cover"):
                        it["meta"]["cover"] = it["cover"]   # Spotify's album picture
                    if it["meta"].pop("swapped", False):   # it was "Song - Artist"
                        it["artist"], it["title"] = it["title"], it["artist"]
                        it["reliable"] = True
                        renamed = True
                    it["meta"]["country_name"] = country_name(it["meta"].get("country", ""))
                    it["state"] = "found" if (it["meta"].get("genre") or it["meta"].get("tags")) else "unknown"
                    if renamed and not b.get("allow_dupes"):   # now we know its real name: do we have it already?
                        have = _already(it, items)
                        if have:
                            it["existing"], it["state"] = have, "have"
                except Exception as e:
                    it["meta"], it["state"] = None, "error"
                    failures += 1
                    if failures == 1:
                        log(msg("log.pv_lookup_failed", "Genre lookup unavailable ({error}); you can still download.", error=e))
                job["recent"].append(it["id"])
                progress(i, len(new))
            look.save()
            log(msg("log.pv_ready", "Ready. Check the list, choose how to sort, then download."))
            return {"name": job["name"] or "Downloads", "items": items}
        return start_job("preview", fn)

    def _already(it, items):
        t = lib.find_existing(artist=it["artist"], title=it["title"],
                              source=f"youtube:{it['video_id']}" if it.get("video_id") else None,
                              scope=lib.config()["dupe_scope"])
        if t:
            return f"{t.folder}/{t.filename}"
        key = song_key(it["artist"], it["title"])
        for other in items:
            if other is it:
                break
            if not other.get("existing") and not other.get("unavailable") and same_song(key, song_key(other["artist"], other["title"])):
                return "listed twice"
        return None

    @app.post("/api/lookup")
    def lookup_one():
        """Look up one song again, after the user corrected its artist or title."""
        b = body()
        title = (b.get("title") or "").strip()
        if not title:
            return err(msg("err.no_title", "Type the song's title"))
        look = lookup()
        info = look.lookup((b.get("artist") or "").strip(), title, {"reliable": True, "text": b.get("text", "")})
        info.pop("swapped", None)
        info["country_name"] = country_name(info.get("country", ""))
        look.save()
        return jsonify({"meta": info, "state": "found" if (info.get("genre") or info.get("tags")) else "unknown"})

    @app.post("/api/sort")
    def sort():
        """Where each song would go for a given way of sorting."""
        b = body()
        mode, folder = b.get("mode", "one"), (b.get("folder") or "").strip()
        rules = b.get("rules") if b.get("rules") is not None else lib.config()["rules"]
        labels = i18n.labels(b.get("labels"))
        out = [sorter.destination(mode, it.get("artist", ""), it.get("meta"), folder=folder, rules=rules, labels=labels)
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
                return err(msg("err.no_links", "Paste at least one link"))

            def simple(log, progress, job):
                d = downloader_cls(lib, log=log, allow_dupes=bool(b.get("allow_dupes")))
                return [d.download(u, folder).to_dict() for u in urls]
            return start_job("download", simple)
        if not items:
            return err(msg("err.no_songs", "Tick at least one song"))
        for it in items:
            if not (it.get("folder") or "").strip() and not it.get("tempo_folder"):
                return err(msg("err.no_folder_for", "Choose a folder for {title}", title=it.get("title")))

        def fn(log, progress, job):
            try:
                d = downloader_cls(lib, log=log, allow_dupes=bool(b.get("allow_dupes")), labels=b.get("labels"))
            except TypeError:  # a downloader without language support
                d = downloader_cls(lib, log=log, allow_dupes=bool(b.get("allow_dupes")))
            job["items"] = items   # each song gets a live status: downloading 40 %, converting, done…
            count = [0]

            def after(_):
                count[0] += 1
                progress(count[0], len(items))
            return d.download_items(items, after_each=after, should_stop=lambda: job["cancel"]).to_dict()
        return start_job("download", fn)

    # ---------- sorting rules & organizing the library ----------
    def fill(tracks, log, progress, job):
        """Look up songs, telling the dashboard which song is being read and what was found."""
        last = []  # the song being looked up (kept out of `job`, which is sent to the browser as JSON)

        def report(t):
            job["recent"].append({"song": f"{t.artist} - {t.title}" if t.artist else t.title, "folder": t.folder, "meta": t.meta})

        def current(t):
            if last:
                report(last.pop())
            job["current"] = f"{t.artist} - {t.title}" if t.artist else t.title
            last.append(t)
        sorter.fill_meta(lib, tracks, log=log, progress=progress, current=current, should_stop=lambda: job["cancel"])
        if last:
            report(last.pop())

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
            fill(tracks, log, progress, job)
            rules = sorter.suggest_rules(tracks)
            log(msg("log.rules_found", "Found {n} rule(s) from your folders.", n=len(rules)) if rules else
                msg("log.rules_none", "Couldn't find anything your folders have in common yet."))
            return {"rules": rules}
        return start_job("suggest", fn)

    @app.post("/api/organize/analyze")
    def organize_analyze():
        folder = body().get("folder") or None

        def fn(log, progress, job):
            tracks = lib.tracks(folder)
            fill(tracks, log, progress, job)
            log(msg("log.analyze_done", "Done. Choose how to sort and check where each song would go."))
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
        return start_job("sync", lambda log, progress, job: device.sync(
            lib, path, mirror=bool(b.get("mirror")), replace=bool(b.get("replace")),
            folders=b.get("folders") or None, log=log, progress=progress))

    # ---------- connected accounts ----------
    @app.get("/api/accounts")
    def accounts_status():
        return jsonify(accounts.status())

    @app.post("/api/accounts/youtube")
    def youtube_connect():
        b = body()
        try:
            accounts.set_youtube_browser(b.get("browser", ""), b.get("profile", ""))
        except ValueError as e:
            return err(str(e))
        return jsonify(accounts.status())

    @app.post("/api/accounts/youtube/cookies")
    def youtube_cookies():
        f = request.files.get("file")
        text = f.read().decode("utf-8", "replace") if f else (body().get("text") or "")
        try:
            accounts.set_youtube_cookies(text)
        except ValueError:
            return err(msg("err.bad_cookies", "This doesn't look like a cookies.txt file with YouTube cookies"))
        return jsonify(accounts.status())

    @app.delete("/api/accounts/youtube")
    def youtube_disconnect():
        accounts.disconnect_youtube()
        return jsonify(accounts.status())

    @app.post("/api/accounts/spotify/start")
    def spotify_start():
        port = request.environ.get("SERVER_PORT") or "8765"
        redirect = f"http://127.0.0.1:{port}/spotify/callback"
        try:
            url = accounts.spotify_start(body().get("client_id", ""), redirect)
        except ValueError:
            return err(msg("err.bad_client_id", "Paste the Client ID from your Spotify app"))
        return jsonify({"url": url, "redirect": redirect})

    @app.get("/spotify/callback")
    def spotify_callback():
        if request.args.get("error"):
            return render_template("spotify_done.html", ok=False, detail=request.args.get("error"))
        try:
            user = accounts.spotify_finish(request.args.get("code", ""), request.args.get("state", ""))
            return render_template("spotify_done.html", ok=True, detail=user)
        except Exception as e:
            return render_template("spotify_done.html", ok=False, detail=str(e))

    @app.delete("/api/accounts/spotify")
    def spotify_disconnect():
        accounts.disconnect_spotify()
        return jsonify(accounts.status())

    @app.post("/api/accounts/playlists")
    def my_playlists():
        """The playlists of every connected account (YouTube can take a few seconds)."""
        def fn(log, progress, job):
            st = accounts.status()
            out, errors = [], {}
            if st["spotify"]["connected"]:
                job["current"] = "Spotify"
                try:
                    out += accounts.spotify_my_playlists()
                except Exception as e:
                    errors["spotify"] = str(e)[:200]
            if st["youtube"]["connected"]:
                job["current"] = "YouTube"
                try:
                    out += downloader_cls(lib, log=log).my_playlists()
                except Exception as e:
                    text = str(e)
                    errors["youtube"] = {"kind": accounts.explain_youtube_error(text), "text": text[:300]}
            return {"playlists": out, "errors": errors}
        return start_job("playlists", fn)

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
            return err(msg("err.cant_update_here", "Start SwimTunes with the app or `swimtunes ui` to update from here"))
        if busy():
            return err(msg("err.busy_update", "Wait until the current download or copy has finished"))

        def fn(log, progress, job):
            cmd = updater.apply(log)
            log(msg("log.upd_restart", "Restarting SwimTunes… this page reloads by itself."))
            app.config["RESTART"] = cmd
            threading.Timer(1.0, app.config["SHUTDOWN"]).start()
            return {"restart": True}
        return start_job("update", fn)

    health_cache: dict = {}

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
            return err(msg("err.quit_terminal", "Stop SwimTunes from the terminal with Ctrl+C"))
        if busy() and not body().get("force"):
            return err(msg("err.busy_quit", "A download or sync is still running"))
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


def _friendly_link_error(e: Exception, url: str) -> Exception:
    """Turn 'playlist does not exist / is private' into advice to connect an account."""
    text = str(e)
    low = text.lower()
    if "spotify" in url and ("private" in low or "connect your spotify" in low):
        return RuntimeError(msg("err.sp_private", "This Spotify playlist is private. Connect your Spotify account in Settings, then try again."))
    if "youtube" in url or "youtu.be" in url:
        if "private" in low or "does not exist" in low or "sign in" in low or "unavailable" in low and "playlist" in low:
            from . import accounts as acc
            if not acc.status()["youtube"]["connected"]:
                return RuntimeError(msg("err.yt_private", "YouTube won't show this playlist: it is private or doesn't exist. "
                                        "If it's yours, connect your YouTube account in Settings, then try again."))
            return RuntimeError(msg("err.yt_private_connected", "YouTube won't show this playlist, even with your account. "
                                    "Check the link, and that you're signed in to the right account in your browser."))
    return e
