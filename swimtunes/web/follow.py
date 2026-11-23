"""Followed playlists: remember a YouTube or Spotify link and get its new songs later.

Each followed playlist is {url, name, folder, auto, added, checked, new, total} in the settings.
Checking reads the link again, counts the songs SwimTunes doesn't have yet and, when "auto"
is on, downloads them into the playlist's folder (in playlist order, like any download).
"""

from __future__ import annotations

import time

from flask import jsonify

from ..i18n import msg
from ..text import safe_name
from .common import _friendly_link_error


def register(app, ctx):
    lib, err, body, start_job, downloader_cls = ctx.lib, ctx.err, ctx.body, ctx.start_job, ctx.downloader_cls

    def followed() -> list[dict]:
        return [dict(f) for f in (lib.config().get("followed") or [])]

    def save(items: list[dict]) -> None:
        lib.set_config(followed=items)

    @app.get("/api/follow")
    def follow_list():
        return jsonify(followed())

    @app.post("/api/follow")
    def follow_add():
        b = body()
        url, folder = (b.get("url") or "").strip(), safe_name((b.get("folder") or "").strip())
        if not url.startswith(("http://", "https://", "spotify:")):
            return err(msg("err.follow_link", "Follow works with one playlist link at a time"))
        if not folder:
            return err(msg("err.no_folder_for", "Choose a folder for {title}", title=b.get("name") or url))
        items = [f for f in followed() if f["url"] != url]
        items.append({"url": url, "name": (b.get("name") or folder).strip(), "folder": folder,
                      "auto": bool(b.get("auto", True)), "added": time.time(), "checked": time.time(), "new": 0,
                      "total": int(b.get("total") or 0)})
        save(items)
        return jsonify(items)

    @app.post("/api/follow/update")
    def follow_update():
        b = body()
        items = followed()
        for f in items:
            if f["url"] == b.get("url"):
                if "auto" in b:
                    f["auto"] = bool(b["auto"])
        save(items)
        return jsonify(items)

    @app.post("/api/follow/remove")
    def follow_remove():
        url = body().get("url")
        save([f for f in followed() if f["url"] != url])
        return jsonify(followed())

    @app.post("/api/follow/check")
    def follow_check():
        """Look for new songs in every followed playlist (or one: {"url"}); download them
        when the playlist has "auto" on, or when {"download": true} is sent."""
        b = body()
        only, force_download = b.get("url"), bool(b.get("download"))

        def run(log, progress, job):
            d = downloader_cls(lib, log=log)
            items = followed()
            todo = [f for f in items if not only or f["url"] == only]
            report = []
            for i, f in enumerate(todo, 1):
                if job["cancel"]:
                    break
                job["current"] = f["name"]
                progress(i - 1, len(todo))
                log(msg("log.follow_checking", "Checking {name} for new songs…", name=f["name"]))
                try:
                    data = d.resolve(f["url"])
                except Exception as e:   # one broken link shouldn't stop the others
                    log(_friendly_link_error(e, f["url"]))
                    f.update(error=str(e)[:200], checked=time.time())
                    save([f if g["url"] == f["url"] else g for g in followed()])
                    report.append({"url": f["url"], "error": True})
                    continue
                f.pop("error", None)
                new = [it for it in data["items"] if not it.get("existing")]
                f.update(checked=time.time(), total=len(data["items"]), new=len(new))
                if f.get("name") in (None, "", f["folder"]) and data.get("name"):
                    f["name"] = data["name"]
                downloaded = 0
                if new and (f.get("auto") or force_download):
                    log(msg("log.follow_downloading", "{name}: {n} new song(s), downloading into {folder}",
                            name=f["name"], n=len(new), folder=f["folder"]))
                    for it in new:
                        it["folder"] = f["folder"]
                    job["items"] = new
                    res = d.download_items(new, should_stop=lambda: job["cancel"])
                    downloaded = len(res.downloaded)
                    f["new"] = max(0, len(new) - downloaded)
                elif new:
                    log(msg("log.follow_found", "{name}: {n} new song(s)", name=f["name"], n=len(new)))
                report.append({"url": f["url"], "new": f["new"], "downloaded": downloaded})
                save([f if g["url"] == f["url"] else g for g in followed()])
            progress(len(todo), len(todo))
            return {"playlists": report, "downloaded": sum(r.get("downloaded", 0) for r in report)}
        return start_job("follow", run)
