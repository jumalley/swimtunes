"""YouTube and Spotify accounts, and the user's own playlists."""

from __future__ import annotations

from flask import jsonify, render_template, request

from .. import accounts
from ..i18n import msg


def register(app, ctx):
    lib, err, body, start_job, downloader_cls = (ctx.lib, ctx.err, ctx.body, ctx.start_job, ctx.downloader_cls)

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

