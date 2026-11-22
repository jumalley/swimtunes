import json
import subprocess
import time
from pathlib import Path

import pytest

from swimtunes import device, spotify, tools
from swimtunes.downloader import Downloader, score_search_result, youtube_meta
from swimtunes.library import Library, LibraryError, write_tags
from swimtunes.text import norm_title, safe_name, same_song, song_key


def make_audio(path: Path, seconds: float = 2, freq: int = 440) -> Path:
    subprocess.run([tools.ffmpeg_path(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=f={freq}:d={seconds}",
                    "-c:a", "libmp3lame", "-b:a", "64k", str(path)], check=True)
    return path


def add_song(lib: Library, folder: str, artist: str, title: str, source=None, seconds=2) -> Path:
    lib.create_folder(folder)
    p = make_audio(lib.root / folder / f"{artist} - {title}.mp3", seconds)
    write_tags(p, title=title, artist=artist, album=folder, source=source, track_no=lib.next_track_no(folder))
    return p


@pytest.fixture
def lib(tmp_path):
    return Library(tmp_path / "lib")


# ---------- text ----------
@pytest.mark.parametrize("a,b", [
    ("Blinding Lights (Official Video)", "Blinding Lights"),
    ("Blinding Lights [Official Audio]", "blinding lights"),
    ("Mr. Brightside - Remastered 2011", "Mr Brightside"),
    ("Señorita (Lyrics)", "Senorita"),
    ("Stay (feat. Justin Bieber)", "Stay"),
    ("Stay ft. Justin Bieber", "Stay"),
    ("Song | Official Music Video HD", "Song"),
])
def test_same_title(a, b):
    assert norm_title(a) == norm_title(b)


@pytest.mark.parametrize("a,b", [
    ("Blinding Lights", "Blinding Lights (Remix)"),
    ("Hurt", "Hurt (Live)"),
    ("Dancing With Myself", "Dancing"),
])
def test_different_title(a, b):
    assert norm_title(a) != norm_title(b)


def test_artist_matching():
    assert same_song(song_key("The Weeknd", "Blinding Lights"), song_key("Weeknd", "Blinding Lights (Official Video)"))
    assert same_song(song_key("Kid LAROI, Justin Bieber", "Stay"), song_key("The Kid LAROI - Topic", "STAY"))
    assert same_song(song_key("", "Stay"), song_key("Kid LAROI", "Stay"))
    assert not same_song(song_key("Rihanna", "Stay"), song_key("Kid LAROI", "Stay"))


def test_safe_name():
    assert safe_name('AC/DC: "Back" in Black?') == "ACDC Back in Black"
    assert safe_name("...") == "untitled"


def test_youtube_meta():
    assert youtube_meta({"title": "Daft Punk - One More Time (Official Video)", "channel": "Daft Punk"}) == ("Daft Punk", "One More Time")
    assert youtube_meta({"title": "Levitating", "channel": "Dua Lipa - Topic"}) == ("Dua Lipa", "Levitating")
    assert youtube_meta({"title": "x", "track": "Levitating", "artist": "Dua Lipa"}) == ("Dua Lipa", "Levitating")


def test_search_scoring_prefers_studio_version():
    results = [
        {"id": "live", "title": "Coldplay - Yellow (Live in Paris)", "channel": "Coldplay", "duration": 300},
        {"id": "cover", "title": "Yellow - Coldplay (cover)", "channel": "Some Guy", "duration": 268},
        {"id": "topic", "title": "Yellow", "channel": "Coldplay - Topic", "duration": 269},
        {"id": "mv", "title": "Coldplay - Yellow (Official Video)", "channel": "Coldplay", "duration": 290},
    ]
    best = min(results, key=lambda e: score_search_result(e, "Coldplay", "Yellow", 269))
    assert best["id"] == "topic"


# ---------- library ----------
def test_stats_and_folders(lib):
    add_song(lib, "Swim", "A", "One", seconds=2)
    add_song(lib, "Swim", "B", "Two", seconds=3)
    add_song(lib, "Chill", "C", "Three", seconds=1)
    s = lib.stats()
    swim = next(f for f in s["folders"] if f["name"] == "Swim")
    assert swim["count"] == 2 and 4.5 < swim["duration"] < 5.5
    assert s["count"] == 3 and s["capacity"] == 4 * 1000**3
    assert [t.track_no for t in lib.tracks("Swim")] == [1, 2]

    lib.rename_folder("Chill", "Cool Down")
    assert lib.folders() == ["Cool Down", "Swim"]
    lib.move_track("Swim/A - One.mp3", "Cool Down")
    assert {t.title for t in lib.tracks("Cool Down")} == {"One", "Three"}
    lib.delete_track("Swim/B - Two.mp3")
    assert lib.tracks("Swim") == []
    assert list((lib.root / ".trash").rglob("*.mp3"))
    lib.delete_folder("Swim")
    assert lib.folders() == ["Cool Down"]
    assert lib.empty_trash() == 1


def test_path_safety(lib):
    lib.create_folder("Swim")
    for bad in ["../x", ".trash", "a/b", ""]:
        with pytest.raises(LibraryError):
            lib.folder_path(bad)
    with pytest.raises(LibraryError):
        lib.resolve_track("../../etc/passwd")


def test_duplicates(lib):
    add_song(lib, "Swim", "The Weeknd", "Blinding Lights")
    time.sleep(0.02)
    add_song(lib, "Chill", "Weeknd", "Blinding Lights (Official Video)")
    add_song(lib, "Chill", "Other", "Song", source="youtube:abc")
    add_song(lib, "Swim", "Other", "Different Name", source="youtube:abc")
    add_song(lib, "Swim", "Other", "Song (Live)")
    groups = lib.duplicates("library")
    assert len(groups) == 2
    bl = next(g for g in groups if "Blinding" in g[0].title)
    assert bl[0].folder == "Swim"  # oldest is kept
    assert lib.duplicates("folder") == []


def test_config(lib):
    lib.set_config(capacity_gb="32", normalize="yes")
    assert lib.config()["capacity_gb"] == 32 and lib.config()["normalize"] is True
    with pytest.raises(LibraryError):
        lib.set_config(nope=1)


# ---------- downloader ----------
class FakeDownloader(Downloader):
    """Pretends to be YouTube."""
    VIDEOS = {
        "v1": {"id": "v1", "title": "Daft Punk - One More Time (Official Video)", "channel": "Daft Punk", "duration": 320},
        "v2": {"id": "v2", "title": "Get Lucky", "channel": "Daft Punk - Topic", "duration": 248},
        "v3": {"id": "v3", "title": "Daft Punk - One More Time [Lyrics]", "channel": "LyricsGuy", "duration": 321},
    }

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fetched = []

    def list_entries(self, url):
        if "list=" in url:
            return "My Swim Mix", [dict(self.VIDEOS[i]) for i in ("v1", "v2", "v3", "v1")]
        return "", [dict(self.VIDEOS[url.split("v=")[1]])]

    def video_info(self, vid):
        return dict(self.VIDEOS[vid])

    def search(self, query, n=6):
        return [{"id": "s-live", "title": query + " (Live)", "channel": "x", "duration": 200},
                {"id": "s-good", "title": query + " (Official Audio)", "channel": "x", "duration": 180}]

    def fetch_cover(self, it, workdir):
        return None   # no network in tests

    def fetch_audio(self, vid, tmpdir):
        self.fetched.append(vid)
        return make_audio(tmpdir / f"{vid}.wav", 1)


def test_download_playlist_skips_dupes(lib):
    logs = []
    d = FakeDownloader(lib, log=logs.append)
    res = d.download("https://www.youtube.com/playlist?list=PL1")
    assert res.folder == "My Swim Mix"
    assert d.fetched == ["v1", "v2"]  # v3 = same song as v1, 4th entry = v1 again
    assert len(res.skipped) == 2
    files = sorted(p.name for p in (lib.root / "My Swim Mix").iterdir())
    assert files == ["Daft Punk - Get Lucky.mp3", "Daft Punk - One More Time.mp3"]
    t = lib.tracks("My Swim Mix")
    assert {x.source for x in t} == {"youtube:v1", "youtube:v2"}
    assert all(x.duration > 0.5 for x in t)

    # downloading into another folder: still skipped (library-wide)
    d2 = FakeDownloader(lib, log=logs.append)
    res2 = d2.download("https://www.youtube.com/watch?v=v2", "Other")
    assert d2.fetched == [] and len(res2.skipped) == 1
    # folder scope: allowed in a different folder
    d3 = FakeDownloader(lib, log=logs.append, scope="folder")
    d3.download("https://www.youtube.com/watch?v=v2", "Other")
    assert d3.fetched == ["v2"]


def test_download_spotify(lib, monkeypatch):
    monkeypatch.setattr(spotify, "fetch", lambda url: {"name": "Swim/Run", "tracks": [
        {"title": "Levitating", "artist": "Dua Lipa", "duration": 180},
        {"title": "One More Time - Remastered", "artist": "Daft Punk", "duration": 320},
    ]})
    add_song(lib, "Old", "Daft Punk", "One More Time")
    d = FakeDownloader(lib, log=lambda _: None)
    res = d.download("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=x")
    assert res.folder == "SwimRun"
    assert d.fetched == ["s-good"]
    assert res.downloaded == ["Dua Lipa - Levitating"] and len(res.skipped) == 1


def test_spotify_parsing():
    data = {"props": {"pageProps": {"state": {"data": {"entity": {
        "type": "playlist", "name": "Beast Mode",
        "trackList": [{"title": "Till I Collapse", "subtitle": "Eminem, Nate Dogg", "duration": 297000}],
    }}}}}}
    html = f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'
    out = spotify.parse_embed_html(html)
    assert out == {"name": "Beast Mode", "tracks": [{"title": "Till I Collapse", "artist": "Eminem, Nate Dogg", "duration": 297.0}]}
    assert spotify.parse_url("https://open.spotify.com/intl-fr/album/abc123?si=1") == ("album", "abc123")
    assert spotify.is_spotify("spotify:track:xyz")
    assert not spotify.is_spotify("https://youtube.com/watch?v=1")


# ---------- device sync ----------
def test_sync(lib, tmp_path, monkeypatch):
    dev = tmp_path / "SWIM"
    dev.mkdir()
    add_song(lib, "Swim", "Z", "First")
    add_song(lib, "Swim", "A", "Second")
    (dev / "Old Stuff").mkdir()
    make_audio(dev / "Old Stuff" / "x.mp3", 1)
    (dev / "._junk.mp3").write_bytes(b"x")
    (dev / "Notes").mkdir()
    (dev / "Notes" / "readme.txt").write_text("keep me")

    # Simulate FAT32: the folder lists files in the order they were copied.
    copied = []
    real_copy = device.shutil.copyfile
    def copy(src, dst):
        copied.append(Path(dst).name)
        return real_copy(src, dst)
    monkeypatch.setattr(device.shutil, "copyfile", copy)
    monkeypatch.setattr(device, "_play_order", lambda folder: [n for n in copied if (folder / n).exists()])

    s = device.sync(lib, dev, log=lambda _: None)
    assert s["updated"] == ["Swim"] and s["junk_removed"] == 1
    assert copied == ["Z - First.mp3", "A - Second.mp3"]  # playlist order, not A-Z
    assert device.sync(lib, dev, log=lambda _: None)["updated"] == []       # nothing changed
    s = device.sync(lib, dev, mirror=True, log=lambda _: None)
    assert s["removed"] == ["Old Stuff"] and (dev / "Notes").exists()


# ---------- web ----------
def test_web(lib):
    from swimtunes.web import create_app
    add_song(lib, "Swim", "A", "One")
    add_song(lib, "Swim", "A", "One (Official Video)")
    c = create_app(lib, downloader_cls=FakeDownloader).test_client()
    assert c.get("/").status_code == 200
    j = c.get("/api/library").get_json()
    assert j["count"] == 2 and j["duplicates"] == 1
    assert len(c.get("/api/folders/Swim").get_json()) == 2
    assert c.post("/api/folders", json={"name": "New"}).get_json() == {"name": "New"}
    assert c.post("/api/folders/New/rename", json={"name": "Newer"}).status_code == 200
    assert c.post("/api/tracks/move", json={"path": "Swim/A - One.mp3", "folder": "Newer"}).status_code == 200
    assert c.post("/api/tracks/move", json={"path": "../x.mp3", "folder": "Newer"}).status_code == 400
    assert len(c.get("/api/duplicates").get_json()) == 1
    assert c.get("/api/duplicates?scope=folder").get_json() == []

    jid = c.post("/api/download", json={"urls": "https://www.youtube.com/watch?v=v2", "folder": "Newer"}).get_json()["id"]
    for _ in range(100):
        job = c.get(f"/api/jobs/{jid}").get_json()
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert job["status"] == "done", job["log"]
    assert job["result"][0]["downloaded"] == ["Daft Punk - Get Lucky"]
    assert c.delete("/api/folders/Newer").status_code == 200


# ---------- double-click app ----------
def test_tools_found():
    assert tools.ffmpeg_path() and tools.deno_path()


def test_find_drives_runs():
    assert isinstance(device.find_drives(), list)


def test_version_and_quit(lib):
    from swimtunes.web import _newer, create_app
    app = create_app(lib)
    c = app.test_client()
    v = c.get("/api/version").get_json()
    assert v["app"] == "swimtunes" and v["can_quit"] is False and v["update"] is None
    assert c.post("/api/quit", json={}).status_code == 400   # not started by the app launcher
    stopped = []
    app.config["SHUTDOWN"] = lambda: stopped.append(1)
    assert c.post("/api/quit", json={}).status_code == 200
    time.sleep(0.5)
    assert stopped == [1]
    assert _newer("v0.2.10", "v0.2.9") and not _newer("v0.2.9", "v0.2.9") and not _newer("v0.2.9", "dev")
    assert c.get("/api/drives").status_code == 200


def test_serve_reuses_running_instance(lib, monkeypatch):
    from swimtunes import app as appmod
    opened = []
    monkeypatch.setattr(appmod, "_running_swimtunes", lambda port: True)
    monkeypatch.setattr(appmod.webbrowser, "open", opened.append)
    assert appmod.serve(lib, port=8765) == 0
    assert opened == ["http://localhost:8765"]
