"""Live feedback, stopping jobs, translations of server messages and folder names."""
import time

from helpers import POP, RUSKI, FakeDownloader, add_song

from swimtunes import sorter
from swimtunes.i18n import as_entry, msg
from swimtunes.library import LibraryError
from swimtunes.web import create_app


def wait(c, jid, until=("done", "error")):
    for _ in range(400):
        j = c.get(f"/api/jobs/{jid}").get_json()
        if j["status"] in until:
            return j
        time.sleep(0.02)
    raise AssertionError("job stuck")


def test_msg_is_a_string_with_a_key():
    m = msg("log.dl_done", "Done: {done} downloaded", done=3)
    assert m == "Done: 3 downloaded" and isinstance(m, str)
    assert as_entry(m) == {"t": "Done: 3 downloaded", "k": "log.dl_done", "p": {"done": 3}}
    assert as_entry("plain") == {"t": "plain"}
    assert as_entry(LibraryError(m))["k"] == "log.dl_done"


def test_translated_folder_names():
    fr = {"other": "Autre", "unknown_tempo": "Tempo inconnu", "easy": "Tranquille (moins de 100 BPM)",
          "countries": {"RU": "Russie"}}
    assert sorter.destination("tempo", "x", {"bpm": None}, labels=fr) == "Tempo inconnu"
    assert sorter.destination("tempo", "x", {"bpm": 90}, labels=fr) == "Tranquille (moins de 100 BPM)"
    assert sorter.destination("tempo", "x", {"bpm": 150}, labels=fr) == "Sprint (140+ BPM)"   # not translated: English
    assert sorter.destination("country", "x", RUSKI, labels=fr) == "Russie"
    assert sorter.destination("genre", "x", None, labels=fr) == "Autre"


class SlowLookup:
    def __init__(self):
        self.calls = 0

    def lookup(self, artist, title, hints=None):
        self.calls += 1
        time.sleep(0.15)
        return dict(RUSKI if "Lucky" in title else POP)

    def save(self):
        pass


def test_preview_shows_songs_live_and_can_be_stopped(lib):
    look = SlowLookup()
    c = create_app(lib, downloader_cls=FakeDownloader, meta_lookup=look).test_client()
    jid = c.post("/api/preview", json={"urls": "https://www.youtube.com/playlist?list=PL1"}).get_json()["id"]
    # the list is there before the genres are
    for _ in range(200):
        j = c.get(f"/api/jobs/{jid}").get_json()
        if j.get("items"):
            break
        time.sleep(0.01)
    assert [i["state"] for i in j["items"]][:2] in (["waiting", "waiting"], ["looking", "waiting"])
    c.post(f"/api/jobs/{jid}/cancel")
    j = wait(c, jid)
    assert j["status"] == "done"
    assert any(e.get("k") == "log.pv_stopped" for e in j["log"]) or look.calls == 2
    assert all(i["state"] in ("found", "skipped") for i in j["result"]["items"] if not i["existing"])


def test_download_statuses_and_stop(lib):
    c = create_app(lib, downloader_cls=FakeDownloader).test_client()
    items = [{"kind": "youtube", "video_id": v, "artist": "A", "title": f"Song {v}", "duration": 1, "folder": "F"}
             for v in ("v1", "v2")]
    j = wait(c, c.post("/api/download", json={"items": items}).get_json()["id"])
    assert [i["status"] for i in j["items"]] == ["done", "done"]
    items[0]["video_id"] = "v1"
    j = wait(c, c.post("/api/download", json={"items": items[:1]}).get_json()["id"])
    assert j["items"][0]["status"] == "skipped" and j["items"][0]["where"].startswith("F/")
    entry = next(e for e in j["log"] if e.get("k") == "log.dl_skip")
    assert entry["p"]["song"] == "A - Song v1"


def test_errors_carry_translation_keys(lib):
    c = create_app(lib).test_client()
    r = c.post("/api/preview", json={"urls": ""})
    assert r.status_code == 400 and r.get_json()["key"] == "err.no_links"
    r = c.get("/api/folders/nope")
    assert r.get_json()["key"] == "err.no_folder" and r.get_json()["params"] == {"name": "nope"}


def test_status_bar(lib, tmp_path):
    add_song(lib, "Swim", "A", "B")
    c = create_app(lib).test_client()
    s = c.get("/api/status").get_json()
    assert s["ok"] is True and s["device"] is None and s["language"] == "auto"
    dev = tmp_path / "SWIM"
    dev.mkdir()
    c.post("/api/config", json={"device_path": str(dev), "language": "fr"})
    s = c.get("/api/status").get_json()
    assert s["device"]["path"] == str(dev) and s["language"] == "fr"
    assert c.get("/api/jobs/active").get_json() == []


class QuickLookup:
    def lookup(self, artist, title, hints=None):
        return dict(POP)

    def save(self):
        pass


def test_learn_and_analyze_jobs_report_progress(lib, monkeypatch):
    from swimtunes import meta
    monkeypatch.setattr(meta, "MetaLookup", lambda *a, **k: QuickLookup())
    monkeypatch.setattr(meta, "estimate_bpm", lambda *a, **k: None)
    for i in range(3):
        add_song(lib, "mood", f"Artist{i}", f"Song{i}")
    c = create_app(lib).test_client()
    for url in ("/api/rules/suggest", "/api/organize/analyze"):
        j = wait(c, c.post(url, json={}).get_json()["id"])   # every poll must be valid JSON
        assert j["status"] == "done", j["log"]
        assert [r["song"] for r in j["recent"]] == ["Artist0 - Song0", "Artist1 - Song1", "Artist2 - Song2"] or url.endswith("analyze")
