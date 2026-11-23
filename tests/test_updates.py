"""Automatic updates at launch, the SwimTunes.pyw launcher and the tray icon."""
import time
from pathlib import Path

from helpers import wait

from swimtunes.web import create_app

# ---------- protocol (checked against Gadgetbridge's ShokzSupport.encodeCommand) ----------


# ---------- automatic update at launch ----------
def test_auto_update_at_launch(lib, monkeypatch):
    from swimtunes import updater
    applied = []
    monkeypatch.setattr(updater, "apply", lambda log, **kw: applied.append(kw) or ["restart"])
    app = create_app(lib)
    c = app.test_client()
    auto = app.config["MAYBE_AUTO_UPDATE"]
    new = {"available": True, "kind": "app", "latest": "v0.9.1"}
    assert auto({"available": False, "kind": "app"}, wait=0) is None
    assert auto(new, wait=0) is None                     # not started as the app (no restart possible)
    stopped = []
    app.config["SHUTDOWN"] = lambda: stopped.append(1)
    jid = auto(new, wait=0)
    assert wait(c, jid)["status"] == "done" and len(applied) == 1
    assert c.get("/api/version").get_json()["updating"] == jid
    time.sleep(1.2)
    assert stopped == [1] and app.config["RESTART"] == ["restart"]
    assert auto(new, wait=0) is None                     # same version again right after: no update loop
    assert auto({**new, "latest": "v0.9.2"}, wait=0)
    lib.set_config(auto_update=False)
    assert auto({**new, "latest": "v0.9.3"}, wait=0) is None
    assert c.post("/api/config", json={"auto_update": "true"}).status_code == 200 and lib.config()["auto_update"] is True


def test_tray_icon_is_optional():
    import sys as _sys

    from swimtunes import tray
    if _sys.platform != "win32":
        assert tray.start("http://localhost:8765", lambda: None) is None   # only on Windows
    from swimtunes import app as app_mod
    if _sys.platform != "win32":     # on Windows it really opens a message box and waits for a click
        app_mod._error_box("x")      # no-op elsewhere
    app_mod._attach_terminal()


def test_launcher_install_kind_and_update(monkeypatch):
    from swimtunes import updater
    monkeypatch.setenv("SWIMTUNES_LAUNCHER", "/x/SwimTunes.pyw")
    monkeypatch.setenv("SWIMTUNES_LAUNCHER_PYTHON", "/usr/bin/pythonw")
    assert updater.install_kind() == "launcher"
    logs = []
    assert updater.apply(logs.append) == ["/usr/bin/pythonw", "/x/SwimTunes.pyw"]   # the launcher installs it
    assert logs[0].key == "log.upd_launcher"


def test_launcher_file_is_standalone():
    """SwimTunes.pyw must run with a bare Python: standard library only."""
    import ast
    src = (Path(__file__).parents[1] / "SwimTunes.pyw").read_text(encoding="utf-8")
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add((node.module or "").split(".")[0])
    import sys as _sys
    assert mods <= set(_sys.stdlib_module_names), mods - set(_sys.stdlib_module_names)


def test_update_banner_lists_only_whats_new():
    from swimtunes.updater import release_changes
    body = "## What's new\n\n- Faster downloads\n- Battery in the status bar\n\n## Download\n\n- **Windows:** SwimTunes-Setup.exe\n"
    assert release_changes(body) == ["Faster downloads", "Battery in the status bar"]
    assert release_changes("- Old style note") == ["Old style note"]


RELEASES = [  # what GitHub answers, newest first
    {"tag_name": "v0.10.0", "prerelease": True, "draft": False, "body": "## What's new\n- Test version"},
    {"tag_name": "v0.9.0", "prerelease": False, "draft": False, "body": "## What's new\n- Stable"},
    {"tag_name": "v0.8.15", "prerelease": False, "draft": False, "body": ""},
]


def fake_github(path):
    if path.startswith("releases?"):
        return RELEASES
    if path == "releases/latest":
        return RELEASES[1]
    raise AssertionError(path)


def test_channels_and_going_back(monkeypatch):
    from swimtunes import updater
    monkeypatch.setattr(updater, "_github", fake_github)
    monkeypatch.setattr(updater, "install_kind", lambda: "app")
    monkeypatch.setattr(updater, "TAG", "v0.9.0")
    assert updater.check("stable")["available"] is False                    # 0.9.0 is the newest stable
    beta = updater.check("beta")
    assert beta["available"] and beta["latest"] == "v0.10.0" and beta["prerelease"]
    assert beta["changes"] == ["Test version"]
    assert updater.previous_release("v0.10.0")["tag_name"] == "v0.9.0"     # going back skips test versions
    assert updater.previous_release("v0.9.0")["tag_name"] == "v0.8.15"
    assert updater.previous_release("v0.8.15") is None


def test_go_back_through_the_dashboard(lib, monkeypatch, tmp_path):
    from swimtunes import updater
    from swimtunes.web import system
    monkeypatch.setattr(updater, "_github", fake_github)
    monkeypatch.setattr(updater, "install_kind", lambda: "launcher")
    monkeypatch.setattr(system, "TAG", "v0.10.0")
    monkeypatch.setattr(updater, "TAG", "v0.10.0")
    home = tmp_path / "launcher"
    home.mkdir()
    monkeypatch.setenv("SWIMTUNES_LAUNCHER", "/x/SwimTunes.pyw")
    monkeypatch.setenv("SWIMTUNES_LAUNCHER_PYTHON", "/usr/bin/pythonw")
    monkeypatch.setenv("SWIMTUNES_LAUNCHER_HOME", str(home))
    app = create_app(lib)
    c = app.test_client()
    u = c.post("/api/update/channel", json={"channel": "beta"}).get_json()
    assert u["channel"] == "beta" and (home / "channel.txt").read_text() == "beta" and u["previous"] == "v0.9.0"
    assert c.post("/api/update/rollback").get_json()["key"] == "err.cant_update_here"
    app.config["SHUTDOWN"] = lambda: None
    j = wait(c, c.post("/api/update/rollback").get_json()["id"])
    assert j["status"] == "done" and j["result"]["latest"] == "v0.9.0"
    assert (home / "target.txt").read_text() == "v0.9.0" and (home / "skip.txt").read_text() == "v0.10.0"
    assert lib.config()["skip_version"] == "v0.10.0"
    assert app.config["MAYBE_AUTO_UPDATE"]({"available": True, "kind": "app", "latest": "v0.10.0"}, wait=0) is None
