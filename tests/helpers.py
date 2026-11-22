"""Shared test helpers: real (tiny) MP3 files, a pretend YouTube, sample song info."""
import subprocess
import time
from pathlib import Path

from swimtunes import tools
from swimtunes.downloader import Downloader
from swimtunes.library import Library, write_tags


def make_audio(path: Path, seconds: float = 2, freq: int = 440) -> Path:
    subprocess.run([tools.ffmpeg_path(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=f={freq}:d={seconds}",
                    "-c:a", "libmp3lame", "-b:a", "64k", str(path)], check=True)
    return path


def add_song(lib: Library, folder: str, artist: str, title: str, source=None, seconds=2) -> Path:
    lib.create_folder(folder)
    p = make_audio(lib.root / folder / f"{artist} - {title}.mp3", seconds)
    write_tags(p, title=title, artist=artist, album=folder, source=source, track_no=lib.next_track_no(folder))
    return p


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


def tiny_jpeg(path: Path) -> bytes:
    subprocess.run([tools.ffmpeg_path(), "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x36", "-frames:v", "1",
                    str(path)], check=True)
    return path.read_bytes()


DOOMER = {"genre": "Rock", "bpm": None, "year": 2018, "tags": ["post-punk", "coldwave", "dark wave"],
          "country": "BY", "cyrillic": True}


RUSKI = {"genre": "Rock", "bpm": 120, "year": 1988, "tags": ["russian rock", "soviet"], "country": "RU", "cyrillic": True}


POP = {"genre": "Pop", "bpm": 103, "year": 2020, "tags": ["dance-pop", "electropop"], "country": "GB", "cyrillic": False}


def wait(c, jid):
    """Poll a dashboard job until it finishes."""
    for _ in range(300):
        j = c.get(f"/api/jobs/{jid}").get_json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.02)
    raise AssertionError("job did not finish")
