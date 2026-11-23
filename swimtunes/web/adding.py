"""Adding music (preview, sorting, download) and sorting the songs already there."""

from __future__ import annotations

from flask import jsonify

from .. import i18n, sorter
from ..i18n import msg
from ..meta import country_name
from ..text import safe_name, same_song, song_key
from .common import _friendly_link_error


def register(app, ctx):
    lib, err, body, start_job, lookup, downloader_cls = (ctx.lib, ctx.err, ctx.body, ctx.start_job, ctx.lookup, ctx.downloader_cls)

    # ---------- adding music: preview, sort, download ----------
    @app.post("/api/preview")
    def preview():
        b = body()
        import re as _re
        # ignore stray characters typed or copied around a link ("µhttps://…", "<https://…>")
        urls = [(m.group(0).rstrip(">)].,;'\"") if (m := _re.search(r"(?:https?://|spotify:)\S+", u)) else u.strip())
                for u in (b.get("urls") or "").split() if u.strip()]
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
            if not items:
                raise RuntimeError(msg("err.no_songs_found", "No songs found in this link. Check that it's the whole "
                                       "link to a playlist, album or song, and that the playlist isn't empty or private."))
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

