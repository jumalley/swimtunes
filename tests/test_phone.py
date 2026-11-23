"""Using SwimTunes from a phone on the home Wi-Fi: only with the code, never by default."""
from swimtunes.web import create_app
from swimtunes.web import phone as phone_mod

PHONE = {"REMOTE_ADDR": "192.168.1.42"}


def test_phone_needs_the_switch_and_the_code(lib):
    c = create_app(lib).test_client()
    # the computer itself: no code, ever
    assert c.get("/api/library").status_code == 200
    # a phone while the switch is off: refused
    assert c.get("/", environ_base=PHONE).status_code == 403
    lib.set_config(phone_access=True)
    st = c.get("/api/phone").get_json()
    assert st["enabled"] and len(st["code"]) == 6 and st["code"].isdigit()
    # a phone without the code: sent to the code page; the API says why
    r = c.get("/", environ_base=PHONE)
    assert r.status_code == 302 and r.headers["Location"].endswith("/phone/login")
    assert c.get("/api/library", environ_base=PHONE).get_json()["key"] == "err.phone_login"
    page = c.get("/phone/login", environ_base=PHONE, headers={"Accept-Language": "fr-FR,fr;q=0.9"})
    assert "Tapez le code" in page.get_data(as_text=True)
    assert c.get("/manifest.webmanifest", environ_base=PHONE).get_json()["display"] == "standalone"
    # wrong code, then the right one: a cookie lets the phone in from then on
    assert c.post("/phone/login", data={"code": "000000" if st["code"] != "000000" else "111111"}, environ_base=PHONE).status_code == 401
    r = c.post("/phone/login", data={"code": st["code"]}, environ_base=PHONE)
    assert r.status_code == 302 and phone_mod.COOKIE in r.headers["Set-Cookie"] and "HttpOnly" in r.headers["Set-Cookie"]
    assert c.get("/api/library", environ_base=PHONE).status_code == 200
    assert c.get("/api/version", environ_base=PHONE).get_json()["remote"] is True
    assert c.get("/api/version").get_json()["remote"] is False
    # some things only from the computer: quitting it, its folders, and the phone code
    assert c.post("/api/quit", environ_base=PHONE).status_code == 403
    assert c.get("/api/phone", environ_base=PHONE).get_json()["key"] == "err.computer_only"
    # a new code logs every phone out
    c.post("/api/phone", json={"new_code": True})
    assert c.get("/api/library", environ_base=PHONE).status_code == 401


def test_guessing_the_code_is_limited(lib):
    lib.set_config(phone_access=True)
    c = create_app(lib).test_client()
    code = c.get("/api/phone").get_json()["code"]
    wrong = "123456" if code != "123456" else "654321"
    for _ in range(5):
        assert c.post("/phone/login", data={"code": wrong}, environ_base=PHONE).status_code == 401
    # even the right code waits now (1 million codes, 5 tries a minute: years to guess)
    assert c.post("/phone/login", data={"code": code}, environ_base=PHONE).status_code == 429


def test_turning_it_on_restarts_swimtunes(lib, monkeypatch):
    app = create_app(lib)
    c = app.test_client()
    stopped = []
    app.config["SHUTDOWN"] = lambda: stopped.append(1)
    app.config["LAN"] = False
    r = c.post("/api/phone", json={"enabled": True}).get_json()
    assert r["restarting"] and app.config["RESTART"] and lib.config()["phone_access"] is True
    import time
    time.sleep(1.0)
    assert stopped == [1]


def test_qr_code_and_addresses(lib, monkeypatch):
    lib.set_config(phone_access=True)
    monkeypatch.setattr(phone_mod, "lan_addresses", lambda: ["192.168.1.10"])
    app = create_app(lib)
    app.config["LAN"] = True
    st = app.test_client().get("/api/phone").get_json()
    assert st["listening"] and st["urls"][0].startswith("http://192.168.1.10:")
    assert st["qr"].lstrip().startswith("<svg")
