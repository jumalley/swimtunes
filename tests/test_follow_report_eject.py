"""Followed playlists, the problem report and ejecting the earphones."""
import subprocess
from pathlib import Path

from helpers import FakeDownloader, add_song, wait

from swimtunes import device, report
from swimtunes.web import create_app

PLAYLIST = "https://www.youtube.com/playlist?list=PLswim"


def test_follow_a_playlist(lib):
    c = create_app(lib, downloader_cls=FakeDownloader).test_client()
    assert c.post("/api/follow", json={"url": "two links here", "folder": "x"}).get_json()["key"] == "err.follow_link"
    f = c.post("/api/follow", json={"url": PLAYLIST, "name": "My Swim Mix", "folder": "Swim mix", "auto": False}).get_json()
    assert f[0]["folder"] == "Swim mix" and f[0]["auto"] is False
    # not automatic: it only counts the new songs
    j = wait(c, c.post("/api/follow/check", json={}).get_json()["id"])
    assert j["status"] == "done" and j["result"]["downloaded"] == 0
    first = c.get("/api/follow").get_json()[0]
    assert first["new"] == 2 and first["total"] == 4       # 4 entries, 2 distinct songs
    # "Get them": downloads into the playlist's folder, in order
    j = wait(c, c.post("/api/follow/check", json={"url": PLAYLIST, "download": True}).get_json()["id"])
    assert j["result"]["downloaded"] == 2
    assert [t.title for t in lib.tracks("Swim mix")] == ["One More Time", "Get Lucky"]
    j = wait(c, c.post("/api/follow/check", json={}).get_json()["id"])
    assert c.get("/api/follow").get_json()[0]["new"] == 0       # nothing new any more
    # automatic: a song that appears later is downloaded by itself
    FakeDownloader.VIDEOS["v4"] = {"id": "v4", "title": "Daft Punk - Around the World", "channel": "Daft Punk", "duration": 200}
    orig = FakeDownloader.list_entries
    FakeDownloader.list_entries = lambda self, url: ("My Swim Mix", [dict(self.VIDEOS[i]) for i in ("v1", "v2", "v4")])
    try:
        c.post("/api/follow/update", json={"url": PLAYLIST, "auto": True})
        j = wait(c, c.post("/api/follow/check", json={}).get_json()["id"])
        assert j["result"]["downloaded"] == 1 and len(lib.tracks("Swim mix")) == 3
    finally:
        FakeDownloader.list_entries = orig
        del FakeDownloader.VIDEOS["v4"]
    assert c.post("/api/follow/remove", json={"url": PLAYLIST}).get_json() == []
    assert len(lib.tracks("Swim mix")) == 3                      # unfollowing keeps the songs


def test_a_broken_followed_link_does_not_stop_the_others(lib):
    class Broken(FakeDownloader):
        def list_entries(self, url):
            if "broken" in url:
                raise RuntimeError("ERROR: [youtube:tab] broken: The playlist does not exist.")
            return super().list_entries(url)
    c = create_app(lib, downloader_cls=Broken).test_client()
    c.post("/api/follow", json={"url": "https://www.youtube.com/playlist?list=broken", "folder": "a", "auto": False})
    c.post("/api/follow", json={"url": PLAYLIST, "folder": "b", "auto": False})
    j = wait(c, c.post("/api/follow/check", json={}).get_json()["id"])
    assert j["status"] == "done"
    f = {x["folder"]: x for x in c.get("/api/follow").get_json()}
    assert f["a"]["error"] and f["b"]["new"] == 2


def test_problem_report_hides_personal_details(lib, monkeypatch):
    add_song(lib, "doomer", "Molchat Doma", "Sudno")
    lib.set_config(shokz={"name": "OpenSwim Pro by Shokz", "address": "AA:BB:CC:11:22:33", "firmware": "R_13", "battery": 80})
    (lib.root / ".swimtunes.log").write_text(f"started from {Path.home()}/Music\naccess_token=abc123 failed\n")
    c = create_app(lib).test_client()
    text = c.get("/api/report").get_json()["text"]
    assert "SwimTunes problem report" in text and "Health:" in text and "1 songs in 1 folders" in text
    assert "firmware R_13" in text and "AA:BB:CC:11:22:33" not in text
    assert str(Path.home()) not in text and "~/Music" in text
    assert "abc123" not in text and "[hidden]" in text
    assert report._private("ID 12:34:56:78:9a:bc") == "ID XX:XX:XX:XX:XX:XX"


def test_eject(tmp_path, monkeypatch, lib):
    calls = []

    def ok(cmd):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def busy(cmd):
        return subprocess.CompletedProcess(cmd, 1, "", "target is busy")
    monkeypatch.setattr(device.sys, "platform", "linux")
    assert device.eject(tmp_path, run=ok)["ok"] and calls[0][:3] == ["gio", "mount", "-e"]
    r = device.eject(tmp_path, run=busy)
    assert not r["ok"] and "busy" in r["detail"]
    monkeypatch.setattr(device.sys, "platform", "darwin")
    calls.clear()
    assert device.eject(tmp_path, run=ok)["ok"] and calls[0][:2] == ["diskutil", "eject"]
    # through the dashboard: a failure explains what to do
    monkeypatch.setattr(device, "eject", lambda path: {"ok": False, "detail": "busy"})
    c = create_app(lib).test_client()
    r = c.post("/api/eject", json={"device": str(tmp_path)})
    assert r.status_code == 400 and r.get_json()["key"] == "err.eject"
    monkeypatch.setattr(device, "eject", lambda path: {"ok": True, "detail": ""})
    assert c.post("/api/eject", json={"device": str(tmp_path)}).get_json()["ok"]
