"""The optional YouTube token helper: install checks, how downloads use it, the Settings switch.
Nothing is downloaded: the plugin and the helper's source are simulated."""
import hashlib
import io
import subprocess
import zipfile

import pytest
from helpers import wait

from swimtunes import potoken
from swimtunes.downloader import Downloader
from swimtunes.web import create_app


def wheel() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("yt_dlp_plugins/extractor/getpot_bgutil_http.py", "# plugin\n")
        z.writestr("bgutil_ytdlp_pot_provider-2.0.2.dist-info/METADATA", "Name: x\n")
    return buf.getvalue()


def source(extra: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("bgutil-ytdlp-pot-provider-2.0.2/server/", "")
        z.writestr("bgutil-ytdlp-pot-provider-2.0.2/server/src/main.ts", "console.log('pot')\n" + extra)
        z.writestr("bgutil-ytdlp-pot-provider-2.0.2/README.md", "not copied\n")
    return buf.getvalue()


@pytest.fixture
def fake_helper(monkeypatch, tmp_path):
    w = wheel()
    monkeypatch.setattr(potoken, "PLUGIN_SHA256", hashlib.sha256(w).hexdigest())
    (tmp_path / "ref/src").mkdir(parents=True)
    (tmp_path / "ref/src/main.ts").write_text("console.log('pot')\n")
    monkeypatch.setattr(potoken, "SERVER_FINGERPRINT", potoken.fingerprint(tmp_path / "ref"))
    monkeypatch.setattr(potoken.tools, "deno_path", lambda: "/usr/bin/deno")
    ran = []

    def run(cmd, cwd):
        ran.append((cmd, cwd))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return w, run, ran


def test_install_checks_fingerprints(fake_helper):
    w, run, ran = fake_helper
    logs = []
    potoken.install(logs.append, fetch=lambda url: w if url == potoken.PLUGIN_URL else source(), run=run)
    assert potoken.installed()
    assert (potoken.home() / "plugins/bgutil/yt_dlp_plugins/extractor/getpot_bgutil_http.py").exists()
    assert (potoken.home() / "server/src/main.ts").exists() and not (potoken.home() / "server/README.md").exists()
    assert ran[0][0][1:] == ["install", "--allow-scripts=npm:canvas", "--frozen"] and ran[0][1].name == "server"
    assert logs[-1].key == "log.helper_done"
    potoken.remove()
    assert not potoken.installed()


def test_a_changed_download_installs_nothing(fake_helper):
    w, run, _ = fake_helper
    with pytest.raises(RuntimeError) as e:           # the helper's code was altered
        potoken.install(fetch=lambda url: w if url == potoken.PLUGIN_URL else source("evil()"), run=run, log=lambda _: None)
    assert e.value.args[0].key == "err.helper_check" and not potoken.installed()
    with pytest.raises(RuntimeError) as e:           # the plugin was altered
        potoken.install(fetch=lambda url: b"x" + w if url == potoken.PLUGIN_URL else source(), run=run, log=lambda _: None)
    assert e.value.args[0].key == "err.helper_check" and not potoken.home().exists()


def test_downloads_use_it_only_when_switched_on(lib, monkeypatch):
    monkeypatch.setattr(potoken, "installed", lambda: True)
    monkeypatch.setattr(potoken, "ytdlp_options",
                        lambda: {"extractor_args": {"youtubepot-bgutilhttp": {"base_url": ["http://127.0.0.1:4999"]}}})
    d = Downloader(lib, log=lambda _: None)
    assert "youtubepot-bgutilhttp" not in (d._ydl().params.get("extractor_args") or {})     # off by default
    lib.set_config(youtube_helper=True)
    args = d._ydl(extractor_args={"youtube": {"player_client": ["tv"]}}).params["extractor_args"]
    assert args["youtubepot-bgutilhttp"]["base_url"] == ["http://127.0.0.1:4999"]       # the helper…
    assert args["youtube"]["player_client"] == ["tv"]                                     # …and the fallback client


def test_a_broken_helper_never_blocks_a_download(lib, monkeypatch):
    lib.set_config(youtube_helper=True)
    monkeypatch.setattr(potoken, "installed", lambda: True)
    monkeypatch.setattr(potoken, "ytdlp_options", lambda: (_ for _ in ()).throw(OSError("deno crashed")))
    logs = []
    assert "extractor_args" not in Downloader(lib, log=logs.append)._ydl().params
    assert "deno crashed" in logs[0]


def test_settings_switch_installs_then_uses_it(lib, monkeypatch):
    state = {"installed": False}
    monkeypatch.setattr(potoken, "installed", lambda: state["installed"])
    monkeypatch.setattr(potoken, "install", lambda log: state.update(installed=True))
    removed = []
    monkeypatch.setattr(potoken, "remove", lambda: removed.append(1) or state.update(installed=False))
    c = create_app(lib).test_client()
    assert c.get("/api/helper").get_json()["enabled"] is False
    j = wait(c, c.post("/api/helper", json={"enabled": True}).get_json()["id"])     # first time: installs
    assert j["status"] == "done" and lib.config()["youtube_helper"] is True
    assert c.post("/api/helper", json={"enabled": False}).get_json()["enabled"] is False
    assert c.post("/api/helper", json={"enabled": True}).get_json()["installed"] is True   # no reinstall
    c.post("/api/helper/remove")
    assert removed and lib.config()["youtube_helper"] is False
