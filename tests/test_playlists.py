"""Playlist cards: pictures, swims, last update, earphone status."""
import base64
import time

from helpers import add_song, tiny_jpeg, wait

from swimtunes import covers
from swimtunes.library import FOLDER_COVER, write_cover
from swimtunes.web import create_app

# ---------- protocol (checked against Gadgetbridge's ShokzSupport.encodeCommand) ----------


# ---------- playlist cards ----------
def test_folder_stats_for_cards(lib, tmp_path):
    p = add_song(lib, "doomer", "Molchat Doma", "Sudno", seconds=3)
    add_song(lib, "doomer", "Molchat Doma", "Kletka", seconds=3)
    write_cover(p, tiny_jpeg(tmp_path / "c.jpg"))
    d = lib.folder_path("doomer")
    before = d.stat().st_mtime_ns
    lib.set_folder_cover("doomer", b"\xff\xd8fakejpeg")
    assert d.stat().st_mtime_ns == before            # a new picture doesn't count as "updated"
    f = lib.stats()["folders"][0]
    assert f["cover"] and f["pics"] == ["doomer/Molchat Doma - Sudno.mp3"] and f["artists"] == ["Molchat Doma"]
    assert abs(f["updated"] - before / 1e9) < 1 and f["synced"] is None
    assert len(lib.tracks("doomer")) == 2            # the picture is not a song
    lib.rename_folder("doomer", "Doomer wave")
    assert (lib.folder_path("Doomer wave") / FOLDER_COVER).exists()   # the picture moves with the folder


def test_picture_endpoints(lib, tmp_path):
    p = add_song(lib, "mood", "Kino", "Gruppa krovi")
    write_cover(p, tiny_jpeg(tmp_path / "c.jpg"))
    c = create_app(lib).test_client()
    assert c.get("/api/folders/mood/picture").status_code == 404
    # upload a file (any shape: it becomes a 600x600 square)
    r = c.post("/api/folders/mood/picture", data={"file": (open(tmp_path / "c.jpg", "rb"), "c.jpg")},
               content_type="multipart/form-data")
    assert r.status_code == 200
    pic = c.get("/api/folders/mood/picture")
    assert pic.status_code == 200 and pic.data[:2] == b"\xff\xd8"
    # from a song, and a mosaic made in the browser
    assert c.post("/api/folders/mood/picture", json={"song": "mood/Kino - Gruppa krovi.mp3"}).status_code == 200
    data = "data:image/jpeg;base64," + base64.b64encode((tmp_path / "c.jpg").read_bytes()).decode()
    assert c.post("/api/folders/mood/picture", json={"data": data}).status_code == 200
    bad = c.post("/api/folders/mood/picture", json={"data": "data:image/png;base64,AAAA"})
    assert bad.status_code == 400 and bad.get_json()["key"] == "err.bad_picture"
    assert c.post("/api/folders/mood/picture", json={}).get_json()["key"] == "err.no_picture"
    assert c.get("/api/library").get_json()["folders"][0]["cover"]
    ideas = c.get("/api/folders/mood/pictures").get_json()
    assert ideas["songs"] == ["mood/Kino - Gruppa krovi.mp3"] and ideas["web"] == []   # offline in tests
    assert "Kino" in ideas["keywords"]
    c.delete("/api/folders/mood/picture")
    assert c.get("/api/folders/mood/picture").status_code == 404
    assert c.get("/api/folders/nope/pictures").status_code == 404


def test_picture_suggestions_from_deezer(lib):
    add_song(lib, "NieR", "Keiichi Okabe", "Weight of the World")
    calls = []

    def fetch(url):
        calls.append(url)
        if "/search/artist" in url:
            return {"data": [{"name": "Keiichi Okabe", "picture_big": "https://cdn/artist.jpg"}]}
        if "/search/album" in url:
            return {"data": [{"title": "NieR:Automata OST", "cover_big": "https://cdn/album.jpg"}]}
        return {"data": [{"title": "Game music", "picture_xl": "https://cdn/pl.jpg"}]}
    import os
    os.environ.pop("SWIMTUNES_OFFLINE", None)
    try:
        r = covers.suggest(lib.tracks("NieR"), "NieR", fetch=fetch)
    finally:
        os.environ["SWIMTUNES_OFFLINE"] = "1"
    assert [w["kind"] for w in r["web"]] == ["album", "playlist", "artist"]
    assert any("q=NieR" in u for u in calls) and r["keywords"][0] == "NieR"
    assert covers.suggest(lib.tracks("NieR"), "Downloads", fetch=fetch)["keywords"][:1] != ["Downloads"]


def test_sync_remembers_when_folders_were_copied(lib, tmp_path):
    add_song(lib, "doomer", "Molchat Doma", "Sudno")
    dev = tmp_path / "SWIM PRO"
    dev.mkdir()
    c = create_app(lib).test_client()
    j = wait(c, c.post("/api/sync", json={"device": str(dev)}).get_json()["id"])
    assert j["status"] == "done"
    synced = lib.config()["synced"]["doomer"]
    assert time.time() - synced < 60
    assert c.get("/api/library").get_json()["folders"][0]["synced"] == synced
    st = c.get("/api/earphones/folders").get_json()
    assert st["path"] == str(dev) and st["folders"] == {"doomer": "same"}
    assert c.post("/api/config", json={"swim_minutes": "60"}).status_code == 200
    assert lib.config()["swim_minutes"] == 60
