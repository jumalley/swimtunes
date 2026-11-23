"""Download reliability, speed, covers and importing from the earphones."""
import os
import time
from pathlib import Path

import pytest
from helpers import FakeDownloader, add_song, make_audio, tiny_jpeg

from swimtunes import device, tools
from swimtunes.downloader import Downloader, error_kind
from swimtunes.library import read_cover, write_cover, write_tags
from swimtunes.web import create_app


@pytest.fixture(autouse=True)
def fat32_order(monkeypatch):
    monkeypatch.setattr(device, "_play_order",
                        lambda folder: sorted(os.listdir(folder), key=lambda f: (folder / f).stat().st_mtime_ns))


# ---------- errors ----------
@pytest.mark.parametrize("text,kind", [
    ("\x1b[0;31mERROR:\x1b[0m unable to download video data: HTTP Error 403: Forbidden", "blocked"),
    ("[0;31mERROR:[0m unable to download video data: HTTP Error 403: Forbidden", "blocked"),
    ("ERROR: [youtube] x: Sign in to confirm your age. This video may be inappropriate for some users.", "age"),
    ("ERROR: [youtube] x: Private video. Sign in if you've been granted access", "private"),
    ("ERROR: [youtube] x: Sign in to confirm you're not a bot", "bot"),
    ("ERROR: [youtube] x: The uploader has not made this video available in your country", "region"),
    ("ERROR: [youtube] x: Video unavailable. This video has been removed by the uploader", "unavailable"),
    ("no match found on YouTube", "not_found"),
    ("something odd", "other"),
])
def test_error_kinds(text, kind):
    assert error_kind(text) == kind


class FakeYDL:
    """Pretends YouTube refuses the default client (403) and accepts the next one."""
    calls = []

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        clients = (self.opts.get("extractor_args") or {}).get("youtube", {}).get("player_client")
        FakeYDL.calls.append(clients)
        if clients is None:
            raise RuntimeError("\x1b[0;31mERROR:\x1b[0m unable to download video data: HTTP Error 403: Forbidden")
        out = Path(self.opts["outtmpl"].replace("%(id)s", "vid").replace("%(ext)s", "webm"))
        out.write_bytes(b"audio")
        return {"requested_downloads": [{"filepath": str(out)}]}


def test_refused_download_is_retried_with_another_client(lib, tmp_path):
    d = Downloader(lib, log=lambda _: None)
    d._ydl = lambda **opts: FakeYDL(opts)
    FakeYDL.calls = []
    it = {}
    d._item = it
    assert d.fetch_audio("vid", tmp_path).read_bytes() == b"audio"
    assert FakeYDL.calls == [None, ["tv", "web_safari"]] and it["retry"] == 1


def test_errors_are_shown_without_colour_codes(lib):
    class Refused(FakeDownloader):
        def fetch_audio(self, vid, tmpdir):
            raise RuntimeError("\x1b[0;31mERROR:\x1b[0m unable to download video data: HTTP Error 403: Forbidden")
    d = Refused(lib, log=lambda _: None)
    items = [{"kind": "youtube", "video_id": "v1", "artist": "A", "title": "B", "folder": "F"}]
    d.download_items(items)
    assert items[0]["status"] == "failed" and items[0]["error_kind"] == "blocked"
    assert items[0]["error"] == "unable to download video data: HTTP Error 403: Forbidden"


# ---------- speed ----------
class SlowFake(FakeDownloader):
    def fetch_audio(self, vid, tmpdir):
        time.sleep(0.4 if vid == "s1" else 0.05)   # the first song is the slowest
        return super().fetch_audio(vid, tmpdir)


def test_parallel_downloads_keep_playlist_order(lib):
    d = SlowFake(lib, log=lambda _: None)
    items = [{"kind": "youtube", "video_id": f"s{i}", "artist": "A", "title": f"Song {i}", "folder": "F"} for i in range(1, 7)]
    items.append(dict(items[0]))                      # listed twice: skipped without downloading again
    t0 = time.time()
    res = d.download_items(items)
    took = time.time() - t0
    assert [t.title for t in lib.tracks("F")] == [f"Song {i}" for i in range(1, 7)]
    assert [t.track_no for t in lib.tracks("F")] == [1, 2, 3, 4, 5, 6]
    assert d.fetched.count("s1") == 1 and len(res.skipped) == 1
    assert took < 0.4 + 6 * 0.05 + 2   # downloads overlapped


def test_progress_counts_every_song(lib):
    add_song(lib, "F", "A", "Song 1")
    d = FakeDownloader(lib, log=lambda _: None)
    seen = []
    items = [{"kind": "youtube", "video_id": f"s{i}", "artist": "A", "title": f"Song {i}", "folder": "F"} for i in (1, 2)]
    d.download_items(items, after_each=lambda it: seen.append(it["title"]))
    assert sorted(seen) == ["Song 1", "Song 2"]


# ---------- covers ----------
def test_cover_is_embedded_and_served(lib, tmp_path):
    p = add_song(lib, "F", "A", "B")
    img = tiny_jpeg(tmp_path / "c.jpg")
    write_cover(p, img)
    write_tags(p, title="B", artist="A", album="F")      # other tag changes keep the picture
    assert read_cover(p)[0] == img and lib.tracks("F")[0].has_cover
    c = create_app(lib).test_client()
    r = c.get("/api/cover?path=F/A - B.mp3")
    assert r.status_code == 200 and r.data == img and r.mimetype == "image/jpeg"
    assert c.get("/api/cover?path=../x.mp3").status_code == 400


def test_download_embeds_the_cover(lib, tmp_path):
    img = tiny_jpeg(tmp_path / "c.jpg")

    class WithCover(FakeDownloader):
        def fetch_cover(self, it, workdir):
            out = workdir / "cover.jpg"
            out.write_bytes(img)
            return out
    WithCover(lib, log=lambda _: None).download_items(
        [{"kind": "youtube", "video_id": "v1", "artist": "A", "title": "B", "folder": "F"}])
    assert read_cover(lib.tracks("F")[0].path)[0] == img


# ---------- import ----------
def make_earphones(tmp_path):
    dev = tmp_path / "SWIM PRO"
    (dev / "doomer" / "Etazhi").mkdir(parents=True)
    make_audio(dev / "doomer" / "Molchat Doma - Sudno.mp3", 1)
    make_audio(dev / "doomer" / "Etazhi" / "Molchat Doma - Kletka.mp3", 1)   # in a sub-folder
    make_audio(dev / "Kino - Gruppa krovi.mp3", 1)                            # directly on the earphones
    return dev


def test_import_sub_folders_and_loose_songs(lib, tmp_path):
    dev = make_earphones(tmp_path)
    rows = {r["name"]: r for r in device.compare(lib, dev)["folders"]}
    assert rows["doomer"]["on_earphones"] == 2 and rows["doomer"]["only_on_earphones"] == 2
    assert rows["."]["on_earphones"] == 1 and rows["."]["only_on_earphones"] == 1
    reports = []
    res = device.import_from_device(lib, dev, ["doomer", "."], ffmpeg=tools.ffmpeg_path(), log=lambda _: None,
                                    report=reports.append, root_name="Depuis les écouteurs")
    assert res == {"imported": 3, "skipped": 0, "failed": 0}
    assert [t.title for t in lib.tracks("doomer")] == ["Sudno", "Kletka"]
    assert [t.title for t in lib.tracks("Depuis les écouteurs")] == ["Gruppa krovi"]
    assert [r["status"] for r in reports] == ["imported"] * 3


def test_import_already_owned_songs_only_when_asked(lib, tmp_path):
    dev = make_earphones(tmp_path)
    add_song(lib, "mood", "Molchat Doma", "Sudno")
    reports = []
    res = device.import_from_device(lib, dev, ["doomer"], ffmpeg=None, log=lambda _: None, report=reports.append)
    assert res["skipped"] == 1 and reports[0] == {"file": "Molchat Doma - Sudno.mp3", "folder": "doomer",
                                                   "status": "skipped", "where": "mood/Molchat Doma - Sudno.mp3"}
    res = device.import_from_device(lib, dev, ["doomer"], ffmpeg=None, log=lambda _: None, allow_dupes=True)
    assert res["imported"] == 1 and any(t.title == "Sudno" for t in lib.tracks("doomer"))


def test_import_through_the_dashboard_reports_each_file(lib, tmp_path):
    dev = make_earphones(tmp_path)
    c = create_app(lib).test_client()
    jid = c.post("/api/device/import", json={"device": str(dev), "folders": ["doomer", "."], "root_name": "From earphones"}).get_json()["id"]
    for _ in range(300):
        j = c.get(f"/api/jobs/{jid}").get_json()
        if j["status"] in ("done", "error"):
            break
        time.sleep(0.02)
    assert j["status"] == "done", j["log"]
    assert [r["file"] for r in j["recent"]] == ["Molchat Doma - Sudno.mp3", "Etazhi/Molchat Doma - Kletka.mp3", "Kino - Gruppa krovi.mp3"]


# ---------- preview: owned songs noticed after the name is corrected ----------
class RenamingDownloader(FakeDownloader):
    def list_entries(self, url):
        return "mix", [{"id": "zz1"}]

    def video_info(self, vid):
        return {"id": vid, "title": "DAEGHO - Enter the Fuego", "channel": "DAEGHO", "duration": 123}


def test_preview_notices_owned_song_once_its_real_name_is_known(lib):
    add_song(lib, "mood", "DAEGHO", "Enter the Fuego")

    class L:
        def lookup(self, a, t, hints=None):
            return {"genre": "Hip Hop", "bpm": None, "year": None, "tags": [], "country": "", "cyrillic": False}

        def save(self):
            pass
    c = create_app(lib, downloader_cls=RenamingDownloader, meta_lookup=L()).test_client()
    jid = c.post("/api/preview", json={"urls": "https://www.youtube.com/playlist?list=x"}).get_json()["id"]
    for _ in range(300):
        j = c.get(f"/api/jobs/{jid}").get_json()
        if j["status"] in ("done", "error"):
            break
        time.sleep(0.02)
    it = j["result"]["items"][0]
    assert it["title"] == "Enter the Fuego" and it["existing"] == "mood/DAEGHO - Enter the Fuego.mp3"


def test_copy_back_after_import_leaves_no_doubles(lib, tmp_path):
    dev = make_earphones(tmp_path)
    (dev / "doomer" / "Etazhi" / "cover.jpg").write_bytes(b"x")   # something that isn't a song stays
    device.import_from_device(lib, dev, ["doomer", "."], ffmpeg=tools.ffmpeg_path(), log=lambda _: None,
                              root_name="From earphones")
    rows = {r["name"]: r for r in device.compare(lib, dev)["folders"]}
    assert rows["."]["status"] == "imported" and rows["."]["only_on_earphones"] == 0
    device.sync(lib, dev, folders=["doomer"], log=lambda _: None)
    songs = sorted(str(p.relative_to(dev / "doomer")) for p in (dev / "doomer").rglob("*.mp3"))
    assert songs == ["Molchat Doma - Kletka.mp3", "Molchat Doma - Sudno.mp3"]     # each song once, flat
    assert (dev / "doomer" / "Etazhi" / "cover.jpg").exists()


def test_sync_keeps_unknown_songs_in_sub_folders(lib, tmp_path):
    dev = make_earphones(tmp_path)
    add_song(lib, "doomer", "New", "Song")
    s = device.sync(lib, dev, folders=["doomer"], log=lambda _: None)
    assert s["protected"] == [{"folder": "doomer", "only_on_earphones": 2}]
    assert (dev / "doomer" / "Etazhi" / "Molchat Doma - Kletka.mp3").exists()


def test_refused_upload_is_replaced_by_another_upload(lib):
    """YouTube refuses one upload (403, private, region…): the same song is taken from another upload."""
    tried = []

    class Refused(FakeDownloader):
        def fetch_audio(self, vid, tmpdir):
            tried.append(vid)
            if vid == "v2":
                raise RuntimeError("ERROR: [youtube] v2: Video unavailable. This video is not available in your country")
            return super().fetch_audio(vid, tmpdir)

        def search(self, query, n=6):
            return [{"id": "v2", "title": query, "channel": "x", "duration": 248},               # the refused one
                    {"id": "alt-live", "title": query + " (Live)", "channel": "x", "duration": 120},   # too short
                    {"id": "alt-good", "title": query + " (Official Audio)", "channel": "x", "duration": 250}]
    logs = []
    d = Refused(lib, log=logs.append)
    it = {"video_id": "v2", "artist": "Daft Punk", "title": "Get Lucky", "duration": 248, "folder": "Swim"}
    res = d.download_items([it])
    assert res.downloaded == ["Daft Punk - Get Lucky"] and not res.failed
    assert tried == ["v2", "alt-good"] and it["alt_of"] == "v2"
    assert lib.tracks("Swim")[0].source == "youtube:alt-good"
    assert any(getattr(m, "key", "") == "log.dl_alt_upload" for m in logs)


def test_no_other_upload_for_network_errors(lib):
    class Offline(FakeDownloader):
        def fetch_audio(self, vid, tmpdir):
            raise RuntimeError("ERROR: unable to download: <urlopen error [Errno -3] Temporary failure in name resolution> connection")

        def search(self, query, n=6):
            raise AssertionError("no search for a network problem")
    res = Offline(lib, log=lambda _: None).download_items([{"video_id": "v1", "artist": "A", "title": "B", "folder": "F"}])
    assert res.failed and not res.downloaded
