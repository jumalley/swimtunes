"""Finding out what a song is when YouTube says little about it."""
import time

import pytest
from helpers import FakeDownloader

from swimtunes.downloader import youtube_song
from swimtunes.meta import MetaLookup, guess_from_text
from swimtunes.web import create_app


@pytest.mark.parametrize("text,genre,tags", [
    ("The Saiyans Are Coming!! Dragonball Z OST", "Soundtrack", ["anime", "soundtrack"]),
    ("[DBZ full OST] Tenka-Wakeme no Chô-Kessen!!", "Soundtrack", ["anime", "soundtrack"]),
    ("Aogiri Tokyo Ghoul Root A OST~ Disk2 #8", "Soundtrack", ["anime", "soundtrack"]),
    ("Boku no Hero Academia OST #03: My Hero Academia Main Theme", "Soundtrack", ["anime", "soundtrack"]),
    ("Centuries -Fall Out Boy Nightcore", None, ["nightcore"]),
    ("chill lofi beats to study to", "Electronic", ["lo-fi"]),
    ("Molchat Doma - Sudno", None, []),
])
def test_guess_from_text(text, genre, tags):
    g = guess_from_text(text)
    assert g["genre"] == genre and g["tags"] == tags


def test_japanese_titles():
    assert guess_from_text("残酷な天使のテーゼ")["japanese"] is True


def deezer_fake(results):
    calls = []

    def fetch(url, throttle):
        calls.append(url)
        if "deezer.com/search" in url:
            return {"data": results}
        if "deezer.com/track" in url:
            return {"bpm": 120, "release_date": "2014-09-08"}
        if "deezer.com/album" in url:
            return {"genres": {"data": [{"name": "Rock"}]}}
        if "musicbrainz" in url:
            return {"artists": [{"name": "Fall Out Boy", "score": 100, "country": "US", "tags": [{"name": "pop punk", "count": 5}]}]}
        raise AssertionError(url)
    return fetch, calls


def test_song_artist_order_is_fixed(tmp_path):
    fetch, _ = deezer_fake([{"id": 1, "title": "Centuries", "artist": {"name": "Fall Out Boy"}, "album": {"id": 9}}])
    m = MetaLookup(tmp_path / "c.json", fetch=fetch)
    song = youtube_song({"id": "x", "title": "Centuries -Fall Out Boy", "channel": "Nightcore", "duration": 190})
    info = m.lookup(song["artist"], song["title"], {"reliable": song["reliable"], "alt": song["alt"], "text": song["text"]})
    assert info["swapped"] is True and info["genre"] == "Rock" and info["country"] == "US"
    assert info["tags"][0] == "nightcore" and "pop punk" in info["tags"]


def test_title_only_match_may_be_a_cover(tmp_path):
    fetch, calls = deezer_fake([{"id": 1, "title": "Spirited Swordsman Zoro (From \"One Piece\")", "artist": {"name": "Gabriele Motta"},
                                 "album": {"id": 9}}])
    m = MetaLookup(tmp_path / "c.json", fetch=fetch)
    song = youtube_song({"id": "x", "title": "Spirited Swordsman Zoro", "channel": "One Piece Original SoundTrack", "duration": 91})
    assert song["reliable"] is False
    info = m.lookup(song["artist"], song["title"], {"reliable": False, "text": song["text"]})
    # the cover's country/year are not used, and the title gives away a soundtrack
    assert info["country"] == "" and info["year"] is None and info["bpm"] is None
    assert info["genre"] == "Soundtrack" and info["guessed"] and info["tags"][:2] == ["anime", "soundtrack"]
    assert not any("musicbrainz" in c for c in calls)   # the channel name is not looked up as an artist


class DetailsDownloader(FakeDownloader):
    """A playlist where YouTube gives only codes for two videos: one exists, one is private."""
    def list_entries(self, url):
        return "anime songs", [{"id": "EUU9Qew-74Q"}, {"id": "OW1b-nOu7jM"},
                               {"id": "dbz1", "title": "The Saiyans Are Coming!!", "channel": "Dragonball Z OST", "duration": 138}]

    def video_info(self, vid):
        if vid == "OW1b-nOu7jM":
            raise RuntimeError("ERROR: [youtube] OW1b-nOu7jM: Private video. Sign in if you've been granted access")
        if vid == "EUU9Qew-74Q":
            return {"id": vid, "title": "Tokyo Ghoul - Unravel (Opening)", "channel": "Anime Hits", "duration": 90,
                    "tags": ["tokyo ghoul", "anime", "opening"]}
        return {"id": vid, "title": "The Saiyans Are Coming!!", "channel": "Dragonball Z OST", "duration": 138,
                "tags": ["dragon ball z", "ost"], "categories": ["Music"]}


class RecordingLookup:
    def __init__(self):
        self.asked = []

    def lookup(self, artist, title, hints=None):
        from swimtunes.meta import finish
        self.asked.append((artist, title, (hints or {}).get("reliable")))
        return finish({"genre": "", "bpm": None, "year": None, "tags": [], "country": "", "cyrillic": False},
                      artist, title, (hints or {}).get("text", ""))

    def save(self):
        pass


def wait(c, jid):
    for _ in range(400):
        j = c.get(f"/api/jobs/{jid}").get_json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.02)
    raise AssertionError("stuck")


def test_preview_reads_missing_titles_and_flags_private_videos(lib):
    look = RecordingLookup()
    c = create_app(lib, downloader_cls=DetailsDownloader, meta_lookup=look).test_client()
    j = wait(c, c.post("/api/preview", json={"urls": "https://www.youtube.com/playlist?list=PLx"}).get_json()["id"])
    assert j["status"] == "done", j["log"]
    first, private, dbz = j["result"]["items"]
    assert (first["artist"], first["title"], first["duration"]) == ("Tokyo Ghoul", "Unravel (Opening)", 90)
    assert first["meta"]["genre"] == "Soundtrack" and "anime" in first["meta"]["tags"]
    assert private["state"] == "unavailable" and "Private video" in private["unavailable"]
    assert dbz["meta"]["genre"] == "Soundtrack" and dbz["meta"]["guessed"]
    asked_titles = [a[1] for a in look.asked]
    assert "OW1b-nOu7jM" not in asked_titles and "EUU9Qew-74Q" not in asked_titles   # codes are never looked up
    assert ("Dragonball Z OST", "The Saiyans Are Coming!!", False) in look.asked     # channel isn't trusted as artist


def test_fix_a_song_by_hand(lib):
    look = RecordingLookup()
    c = create_app(lib, meta_lookup=look).test_client()
    r = c.post("/api/lookup", json={"artist": "Yutaka Yamada", "title": "Aogiri", "text": "Tokyo Ghoul OST"}).get_json()
    assert look.asked == [("Yutaka Yamada", "Aogiri", True)] and r["meta"]["genre"] == "Soundtrack"
    assert c.post("/api/lookup", json={"title": ""}).get_json()["key"] == "err.no_title"
