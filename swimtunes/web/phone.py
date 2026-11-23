"""Using SwimTunes from a phone on the same Wi-Fi.

SwimTunes runs on the computer (it downloads, converts and copies to the earphones on its
USB cable). With "Use from my phone" on (Settings), it also listens on the home network:
the phone opens http://<computer>:8765, types the 6-digit code shown on the computer once,
and gets a cookie that lets it in from then on. The computer itself never needs the code.
A new code logs every phone out. Off by default; turning it on or off restarts SwimTunes.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import secrets
import socket
import time

from flask import Response, jsonify, make_response, redirect, request

from .. import updater
from ..i18n import msg

COOKIE = "swimtunes_phone"
# only from the computer itself: quitting, opening folders/shortcuts on it, and the phone settings (the code)
LOCAL_ONLY = ("/api/quit", "/api/open", "/api/shortcut", "/api/phone")
OPEN_PATHS = ("/phone/", "/manifest.webmanifest", "/static/icon.png", "/static/icon.ico")
TEXTS = {
    "en": ("Use SwimTunes on this phone", "Type the 6-digit code shown on your computer, in SwimTunes → Settings → Use from my phone.",
           "Code", "Open SwimTunes", "Wrong code. Check it on your computer and try again.", "Too many tries. Wait a minute."),
    "fr": ("Utiliser SwimTunes sur ce téléphone", "Tapez le code à 6 chiffres affiché sur votre ordinateur, dans SwimTunes → Réglages → Utiliser depuis mon téléphone.",
           "Code", "Ouvrir SwimTunes", "Code incorrect. Vérifiez-le sur l’ordinateur et réessayez.", "Trop d’essais. Attendez une minute."),
    "es": ("Usar SwimTunes en este teléfono", "Escribe el código de 6 cifras que aparece en tu ordenador, en SwimTunes → Ajustes → Usar desde mi teléfono.",
           "Código", "Abrir SwimTunes", "Código incorrecto. Compruébalo en el ordenador e inténtalo de nuevo.", "Demasiados intentos. Espera un minuto."),
    "de": ("SwimTunes auf diesem Handy nutzen", "Gib den 6-stelligen Code ein, der auf deinem Computer steht: SwimTunes → Einstellungen → Vom Handy aus nutzen.",
           "Code", "SwimTunes öffnen", "Falscher Code. Prüfe ihn am Computer und versuche es erneut.", "Zu viele Versuche. Warte eine Minute."),
    "it": ("Usa SwimTunes su questo telefono", "Digita il codice di 6 cifre mostrato sul computer, in SwimTunes → Impostazioni → Usa dal telefono.",
           "Codice", "Apri SwimTunes", "Codice errato. Controllalo sul computer e riprova.", "Troppi tentativi. Attendi un minuto."),
    "pt": ("Usar o SwimTunes neste celular", "Digite o código de 6 dígitos mostrado no computador, em SwimTunes → Configurações → Usar pelo celular.",
           "Código", "Abrir o SwimTunes", "Código errado. Confira no computador e tente de novo.", "Tentativas demais. Espere um minuto."),
}


def lan_addresses() -> list[str]:
    """This computer's addresses on the home network (most likely first)."""
    found = []
    try:   # the address used to reach the internet is the one the phone can reach (nothing is sent)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 80))
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    out = []
    for a in found:
        try:
            ip = ipaddress.ip_address(a)
        except ValueError:
            continue
        if ip.is_private and not ip.is_loopback and a not in out:
            out.append(a)
    return out


def _local(addr: str | None) -> bool:
    try:
        return ipaddress.ip_address(addr or "").is_loopback
    except ValueError:
        return False


def _lang() -> str:
    best = request.accept_languages.best_match(list(TEXTS)) if request.accept_languages else None
    return best or "en"


def login_page(error: str = "") -> str:
    title, help_, label, button, *_ = TEXTS[_lang()]
    return f"""<!doctype html><html lang="{_lang()}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>SwimTunes</title>
<link rel="manifest" href="/manifest.webmanifest"><link rel="icon" href="/static/icon.png"><link rel="apple-touch-icon" href="/static/icon.png">
<style>
:root {{ color-scheme: light dark; --bg:#f4f7f9; --panel:#fff; --text:#14212b; --muted:#5d6f7c; --accent:#086d91; --line:#dde5ea; --bad:#c0392b; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0e1418; --panel:#162027; --text:#e4edf2; --muted:#8fa3b0; --accent:#38b2d8; --line:#26343d; --bad:#e5675a; }} }}
body {{ margin:0; min-height:100vh; display:grid; place-items:center; background:var(--bg); color:var(--text);
  font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; padding:16px; box-sizing:border-box; }}
form {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:24px; max-width:380px; width:100%; display:grid; gap:12px; }}
img {{ width:48px; height:48px; }} h1 {{ font-size:20px; margin:0; }} p {{ margin:0; color:var(--muted); font-size:14px; }}
label {{ font-size:13px; color:var(--muted); }}
input {{ font:600 28px/1 ui-monospace,Menlo,Consolas,monospace; letter-spacing:.3em; text-align:center; padding:12px; border-radius:10px;
  border:1px solid var(--line); background:var(--bg); color:var(--text); width:100%; box-sizing:border-box; }}
button {{ font:600 16px system-ui,sans-serif; padding:12px; border-radius:10px; border:0; background:var(--accent); color:#fff; }}
.err {{ color:var(--bad); }}
</style></head><body>
<form method="post" action="/phone/login"><img src="/static/icon.png" alt=""><h1>{title}</h1><p>{help_}</p>
<label for="code">{label}</label><input id="code" name="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]*" maxlength="6" required autofocus>
{f'<p class="err" role="alert">{error}</p>' if error else ''}<button type="submit">{button}</button></form></body></html>"""


def register(app, ctx):
    lib, err, body = ctx.lib, ctx.err, ctx.body
    tries: dict[str, list[float]] = {}

    def settings():
        cfg = lib.config()
        token, code = cfg.get("phone_token"), cfg.get("phone_code")
        if not token or not code:
            token, code = secrets.token_urlsafe(24), f"{secrets.randbelow(10**6):06d}"
            lib.set_config(phone_token=token, phone_code=code)
        return token, code

    @app.before_request
    def phone_guard():
        """Only the computer itself, or a phone that typed the code, may use SwimTunes."""
        if _local(request.remote_addr) or request.path.startswith(OPEN_PATHS):
            return None
        if not lib.config().get("phone_access"):
            return Response("Not allowed", status=403)
        if request.path.startswith(LOCAL_ONLY):
            return err(msg("err.computer_only", "Do this on the computer"), 403)
        token, _ = settings()
        if hmac.compare_digest(request.cookies.get(COOKIE, ""), token):
            return None
        if request.path.startswith("/api/"):
            return err(msg("err.phone_login", "Type the code shown on the computer first"), 401)
        return redirect("/phone/login")

    @app.get("/phone/login")
    def phone_login_page():
        return login_page()

    @app.post("/phone/login")
    def phone_login():
        who = request.remote_addr or "?"
        now = time.time()
        recent = tries[who] = [t for t in tries.get(who, []) if now - t < 60]
        lang_texts = TEXTS[_lang()]
        if len(recent) >= 5:
            return login_page(lang_texts[5]), 429
        token, code = settings()
        if not lib.config().get("phone_access") or not hmac.compare_digest((request.form.get("code") or "").strip(), code):
            recent.append(now)
            return login_page(lang_texts[4]), 401
        resp = make_response(redirect("/"))
        resp.set_cookie(COOKIE, token, max_age=400 * 24 * 3600, httponly=True, samesite="Strict")
        return resp

    @app.get("/manifest.webmanifest")
    def manifest():
        """Lets the phone put SwimTunes on its home screen like an app."""
        return jsonify({"name": "SwimTunes", "short_name": "SwimTunes", "start_url": "/", "display": "standalone",
                        "background_color": "#f4f7f9", "theme_color": "#086d91",
                        "icons": [{"src": "/static/icon.png", "sizes": "512x512", "type": "image/png"}]})

    @app.get("/api/phone")
    def phone_status():
        cfg = lib.config()
        _, code = settings()
        port = request.host.rsplit(":", 1)[-1] if ":" in request.host else "8765"
        urls = [f"http://{a}:{port}" for a in lan_addresses()]
        qr = ""
        if urls and cfg.get("phone_access"):
            try:
                import io

                import segno
                buf = io.BytesIO()
                segno.make(urls[0], error="m").save(buf, kind="svg", scale=5, border=2, dark="#14212b", light="#ffffff", xmldecl=False)
                qr = buf.getvalue().decode()
            except Exception:
                qr = ""
        return jsonify({"enabled": bool(cfg.get("phone_access")), "listening": bool(app.config.get("LAN")),
                        "urls": urls, "code": code if cfg.get("phone_access") else "", "qr": qr,
                        "can_restart": app.config["SHUTDOWN"] is not None})

    @app.post("/api/phone")
    def phone_set():
        """Turn phone access on/off (restarts SwimTunes so it listens on the home network or not)."""
        b = body()
        if b.get("new_code"):
            lib.set_config(phone_token="", phone_code="")      # every phone has to type the new code
            settings()
            return phone_status()
        on = bool(b.get("enabled"))
        lib.set_config(phone_access=on)
        if app.config["SHUTDOWN"] is not None and on != bool(app.config.get("LAN")):
            app.config["RESTART"] = updater.restart_command()
            os.environ["SWIMTUNES_NO_BROWSER"] = "1"
            import threading
            threading.Timer(0.8, app.config["SHUTDOWN"]).start()
            return jsonify({"restarting": True})
        return phone_status()
