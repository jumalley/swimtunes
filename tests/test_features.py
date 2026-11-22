"""Sorting, genre lookup, earphones import/compare, preview flow."""
import os
import time
from pathlib import Path

import pytest

from swimtunes import device, sorter
from swimtunes.library import Library, write_meta, write_tags
from swimtunes.meta import MetaLookup, facts, tempo_label
from test_swimtunes import FakeDownloader, add_song, make_audio

DOOMER = {"genre": "Rock", "bpm": None, "year": 2018, "tags": ["post-punk", "coldwave", "dark wave"],
          "country": "BY", "cyrillic": True}
RUSKI = {"genre": "Rock", "bpm": 120, "year": 1988, "tags": ["russian rock", "soviet"], "country": "RU", "cyrillic": True}
POP = {"genre": "Pop", "bpm": 103, "year": 2020, "tags": ["dance-pop", "electropop"], "country": "GB", "cyrillic": False}


@pytest.fixture
def lib(tmp_path):
    return Library(tmp_path / "lib")


@pytest.fixture(autouse=True)
def fat32_order(monkeypatch):
    """The earphones' FAT32 drive lists files in the order they were copied; simulate that."""
    monkeypatch.setattr(device, "_play_order",
                        lambda folder: sorted(os.listdir(folder), key=lambda f: (folder / f).stat().st_mtime_ns))


# ---------- sorting ----------
def test_destinations():
    d = sorter.destination
    assert d("one", "x", POP, folder="Swim") == "Swim"
    assert d("genre", "x", POP) == "Pop"
    assert d("genre", "x", {"genre": "Rap/Hip Hop"}) == "Rap & Hip Hop"   # no slashes in folder names
    assert d("style", "x", DOOMER) == "Post-Punk"
    assert d("tempo", "x", POP) == "Steady (100-120 BPM)"
    assert d("tempo", "x", DOOMER) == "Unknown tempo"
    assert d("decade", "x", RUSKI) == "1980s"
    assert d("country", "x", DOOMER) == "Belarus"
    assert d("country", "x", {"cyrillic": True}) == "Russian-language"
    assert d("artist", "Dua Lipa feat. DaBaby", POP) == "Dua Lipa"
    assert d("genre", "x", None) == "Other"


def test_rules():
    rules = [{"folder": "ruski", "keywords": ["ru", "soviet"]},
             {"folder": "doomer", "keywords": ["post-punk", "coldwave"]},
             {"folder": "mood", "keywords": ["pop"]}]
    assert sorter.match_rule(rules, RUSKI) == "ruski"
    assert sorter.match_rule(rules, DOOMER) == "doomer"
    assert sorter.match_rule(rules, POP) == "mood"          # 'pop' matches genre 'Pop'
    # 'ru' is short, so it must match exactly: it doesn't match 'russian rock' or 'country: Peru'
    assert sorter.match_rule([{"folder": "r", "keywords": ["ru"]}], {"tags": ["russian rock"]}) is None
    assert sorter.destination("rules", "x", {"genre": "Jazz"}, rules=rules, folder="Inbox") == "Inbox"
    # the best match wins, not the first: Kino is post-punk, but even more Russian
    kino = {"genre": "Rock", "tags": ["russian rock", "post-punk", "soviet"], "country": "RU", "cyrillic": True}
    learned = [{"folder": "doomer", "keywords": ["coldwave", "post-punk", "gothic rock"]},
               {"folder": "ruski", "keywords": ["russian rock", "russia", "cyrillic"]}]
    assert sorter.match_rule(learned, kino) == "ruski"
    assert sorter.match_rule(learned, DOOMER) == "doomer"
    assert "cyrillic" in facts(DOOMER) and "belarus" in facts(DOOMER)


def test_suggest_rules(lib):
    for i, (folder, meta) in enumerate([("doomer", DOOMER), ("doomer", DOOMER), ("doomer", RUSKI),
                                        ("mood", POP), ("mood", POP), ("ruski", RUSKI), ("ruski", RUSKI)]):
        p = add_song(lib, folder, f"Artist{i}", f"Song{i}")
        write_meta(p, meta)
    rules = {r["folder"]: r for r in sorter.suggest_rules(lib.tracks())}
    assert "post-punk" in rules["doomer"]["keywords"]
    assert "pop" in rules["mood"]["keywords"] or "dance-pop" in rules["mood"]["keywords"]
    assert {"russia", "soviet", "russian rock"} & set(rules["ruski"]["keywords"])
    # songs classified with the learned rules land in the right folder
    learned = list(rules.values())
    assert sorter.match_rule(learned, POP) == "mood"


def test_meta_saved_in_mp3(lib):
    p = add_song(lib, "Swim", "Molchat Doma", "Sudno")
    write_meta(p, DOOMER)
    t = lib.tracks("Swim")[0]
    assert t.meta["tags"][0] == "post-punk" and t.to_dict(lib.root)["meta"]["country"] == "BY"
    lib.rename_folder("Swim", "doomer")             # meta survives renames/moves
    assert lib.tracks("doomer")[0].meta["genre"] == "Rock"


# ---------- genre lookup (recorded API answers) ----------
def fake_fetch(url, throttle):
    if "deezer.com/search" in url:
        return {"data": [
            {"id": 1, "title": "Судно (Борис Рыжий)", "artist": {"name": "Molchat Doma"}, "album": {"id": 10}},
            {"id": 2, "title": "Something else", "artist": {"name": "Other"}, "album": {"id": 11}}]}
    if "deezer.com/track/1" in url:
        return {"bpm": 0, "release_date": "2018-09-07"}
    if "deezer.com/album/10" in url:
        return {"genres": {"data": [{"name": "Rock"}]}}
    if "musicbrainz" in url:
        return {"artists": [{"name": "Молчат Дома", "score": 100, "country": "BY",
                             "tags": [{"name": "post-punk", "count": 9}, {"name": "_private", "count": 99},
                                      {"name": "2010s", "count": 5}, {"name": "coldwave", "count": 4}]}]}
    raise AssertionError(url)


def test_meta_lookup(tmp_path):
    m = MetaLookup(tmp_path / "cache.json", fetch=fake_fetch)
    info = m.lookup("Molchat Doma", "Sudno")
    assert info == {"genre": "Rock", "bpm": None, "year": 2018, "tags": ["post-punk", "coldwave"],
                    "country": "BY", "cyrillic": True, "japanese": False}
    m.save()
    again = MetaLookup(tmp_path / "cache.json", fetch=lambda *a: pytest.fail("should use cache"))
    assert again.lookup("Molchat Doma", "Sudno (Official Video)")["genre"] == "Rock"


def test_meta_lookup_survives_one_source_failing(tmp_path):
    def mb_down(url, throttle):
        if "musicbrainz" in url:
            raise OSError("503 busy")
        return fake_fetch(url, throttle)
    m = MetaLookup(tmp_path / "cache.json", fetch=mb_down)
    info = m.lookup("Molchat Doma", "Sudno")
    assert info["genre"] == "Rock" and info["year"] == 2018 and info["tags"] == []
    assert m.cache["songs"] == {}          # not cached: tried again next time
    m.fetch = fake_fetch
    assert m.lookup("Molchat Doma", "Sudno")["tags"] == ["post-punk", "coldwave"]


def test_tempo_labels():
    assert [tempo_label(b) for b in (None, 90, 110, 130, 170)] == [
        "Unknown tempo", "Easy (under 100 BPM)", "Steady (100-120 BPM)", "Fast (120-140 BPM)", "Sprint (140+ BPM)"]


# ---------- earphones ----------
def make_device(tmp_path):
    dev = tmp_path / "SWIM PRO"
    for folder, songs in {"doomer": ["Molchat Doma - Sudno", "Ploho - Kuda"], "mood": ["Dua Lipa - Levitating"],
                          "ruski": ["Kino - Gruppa krovi"]}.items():
        (dev / folder).mkdir(parents=True)
        for s in songs:
            make_audio(dev / folder / f"{s}.mp3", 1)
    import subprocess
    from swimtunes import tools
    subprocess.run([tools.ffmpeg_path(), "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=330:d=1", "-c:a", "aac",
                    str(dev / "mood" / "Lana Del Rey - Video Games.m4a")], check=True)   # not an MP3
    (dev / "System Volume Information").mkdir()
    return dev


def test_compare_and_import(lib, tmp_path):
    dev = make_device(tmp_path)
    add_song(lib, "mood", "Dua Lipa", "Levitating")           # already have one of them
    c = device.compare(lib, dev)
    rows = {r["name"]: r for r in c["folders"]}
    assert set(rows) == {"doomer", "mood", "ruski"}
    assert rows["doomer"]["status"] == "only_on_earphones" and rows["doomer"]["on_earphones"] == 2
    assert rows["mood"]["status"] == "different" and rows["mood"]["only_on_earphones"] == 1   # the .m4a
    logs = []
    res = device.import_from_device(lib, dev, ["doomer", "mood", "ruski"], ffmpeg=__import__("swimtunes.tools").tools.ffmpeg_path(),
                                    log=logs.append)
    assert res == {"imported": 4, "skipped": 1, "failed": 0}
    assert [t.title for t in lib.tracks("doomer")] == ["Sudno", "Kuda"]          # play order kept
    assert any(t.filename == "Lana Del Rey - Video Games.mp3" for t in lib.tracks("mood"))  # converted
    rows = {r["name"]: r for r in device.compare(lib, dev)["folders"]}
    assert rows["doomer"]["status"] == "same" and rows["mood"]["only_on_earphones"] == 0
    # importing again changes nothing
    assert device.import_from_device(lib, dev, ["doomer"], ffmpeg=None, log=logs.append)["imported"] == 0


def test_sync_never_deletes_songs_only_on_earphones(lib, tmp_path):
    dev = make_device(tmp_path)
    add_song(lib, "mood", "New", "Song")       # SwimTunes has a 'mood' folder with different songs
    logs = []
    s = device.sync(lib, dev, log=logs.append)
    assert s["protected"] == [{"folder": "mood", "only_on_earphones": 2}]
    assert (dev / "mood" / "Dua Lipa - Levitating.mp3").exists()
    assert any("Import them first" in l for l in logs)
    device.import_from_device(lib, dev, ["mood"], ffmpeg=__import__("swimtunes.tools").tools.ffmpeg_path(), log=logs.append)
    s = device.sync(lib, dev, log=logs.append)
    assert s["protected"] == [] and "mood" in s["updated"]
    assert len([f for f in os.listdir(dev / "mood")]) == 3   # m4a replaced by its MP3, new song added


def test_capacity_from_drive_size():
    assert device.capacity_gb(31.2e9) == 32 and device.capacity_gb(3.9e9) == 4 and device.capacity_gb(7.8e9) == 8


# ---------- preview -> sort -> download through the dashboard ----------
def test_preview_sort_download(lib):
    from swimtunes.web import create_app
    add_song(lib, "doomer", "Daft Punk", "One More Time")
    meta = {"Daft Punk": POP, "Get Lucky": POP}

    class FakeLookup:
        def lookup(self, artist, title, hints=None):
            return dict(RUSKI if "Lucky" in title else POP)

        def save(self):
            pass

    app = create_app(lib, downloader_cls=FakeDownloader, meta_lookup=FakeLookup())
    c = app.test_client()

    def wait(jid):
        for _ in range(200):
            j = c.get(f"/api/jobs/{jid}").get_json()
            if j["status"] in ("done", "error"):
                return j
            time.sleep(0.05)
        raise AssertionError("job stuck")

    j = wait(c.post("/api/preview", json={"urls": "https://www.youtube.com/playlist?list=PL1"}).get_json()["id"])
    assert j["status"] == "done", j["log"]
    items = j["result"]["items"]
    assert [bool(i["existing"]) for i in items] == [True, False, True, True]   # have it / new / same song / listed twice
    new = [i for i in items if not i["existing"]]
    assert new[0]["meta"]["country"] == "RU"
    folders = c.post("/api/sort", json={"items": new, "mode": "country"}).get_json()["folders"]
    assert folders == ["Russia"]
    c.post("/api/rules", json={"rules": [{"folder": "ruski", "keywords": ["soviet"]}, {"folder": "", "keywords": ["x"]}]})
    assert c.get("/api/rules").get_json()["rules"] == [{"folder": "ruski", "keywords": ["soviet"]}]
    folders = c.post("/api/sort", json={"items": new, "mode": "rules", "folder": "Inbox"}).get_json()["folders"]
    assert folders == ["ruski"]
    new[0]["folder"] = folders[0]
    j = wait(c.post("/api/download", json={"items": new}).get_json()["id"])
    assert j["status"] == "done", j["log"]
    t = lib.tracks("ruski")[0]
    assert t.title == "Get Lucky" and t.meta["country"] == "RU" and t.meta.get("bpm") == 120
    h = c.get("/api/health").get_json()
    assert all(r["ok"] for r in h["tools"])
