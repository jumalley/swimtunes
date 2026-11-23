"""The music library: a folder of playlist folders full of MP3s.

    <library>/
        Swim Mix/
            Artist - Song.mp3
        Chill/
            ...
        .trash/            <- deleted songs/folders go here first
        .swimtunes.json    <- settings
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from mutagen import MutagenError
from mutagen.id3 import APIC, ID3, TALB, TBPM, TCON, TDRC, TIT2, TPE1, TRCK, TXXX, ID3NoHeaderError
from mutagen.mp3 import MP3

from .i18n import msg
from .text import safe_name, same_song, song_key, split_artist_title

SOURCE_TAG = "swimtunes_source"
META_TAG = "swimtunes_meta"
TRASH = ".trash"
CONFIG_FILE = ".swimtunes.json"
DEFAULT_CONFIG = {
    "capacity_gb": 4,          # Shokz OpenSwim = 4 GB, OpenSwim Pro = 32 GB
    "device_path": "",
    "bitrate": 192,
    "normalize": False,        # even out loudness between songs
    "dupe_scope": "library",   # "library" = no song twice anywhere; "folder" = only within a folder
    "rules": [],               # [{"folder": "doomer", "keywords": ["post-punk", "coldwave"]}, ...]
    "sort_mode": "one",        # last used way of sorting new songs
    "language": "auto",        # dashboard language: "auto" (the computer's) or en, fr, es, de, it, pt
    "swim_minutes": 45,        # length of one swim session, for "plays for N swims"
    "synced": {},              # folder -> time it was last copied to / checked on the earphones
    "shokz": {},               # last battery / firmware reading of the earphones (Bluetooth)
    "auto_update": True,       # install new versions by itself when SwimTunes starts
    "auto_update_tried": {},   # {"latest": tag, "time": ...}: never retry the same update in a loop
    "update_channel": "stable",  # "stable" (everyone) or "beta" (test versions first)
    "skip_version": "",          # a version the user went back from: not installed again by itself
    "followed": [],
    "youtube_helper": False,     # use the optional YouTube token helper (see potoken.py)
    "phone_access": False,       # also listen on the home network, for phones (see web/phone.py)
    "phone_token": "",           # what a phone's cookie must hold
    "phone_code": "",            # the 6-digit code a phone types once            # playlists whose new songs SwimTunes fetches (see web/follow.py)
}

FOLDER_COVER = ".swimtunes-cover.jpg"   # a folder's picture (hidden, never copied to the earphones)


def default_library_root() -> Path:
    env = os.environ.get("SWIMTUNES_LIBRARY")
    return Path(env).expanduser() if env else Path.home() / "Music" / "SwimTunes"


@dataclass
class Track:
    path: Path
    folder: str
    title: str
    artist: str
    duration: float
    size: int
    source: str | None
    track_no: int | None
    added: float
    meta: dict | None = None   # genre, bpm, year, tags, country, cyrillic (None = not looked up yet)
    has_cover: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return song_key(self.artist, self.title)

    @property
    def filename(self) -> str:
        return self.path.name

    def to_dict(self, root: Path) -> dict:
        return {
            "path": self.path.relative_to(root).as_posix(),
            "folder": self.folder,
            "filename": self.filename,
            "title": self.title,
            "artist": self.artist,
            "duration": round(self.duration, 1),
            "size": self.size,
            "source": self.source,
            "track_no": self.track_no,
            "meta": _with_country_name(self.meta),
            "has_cover": self.has_cover,
        }


def _with_country_name(meta: dict | None) -> dict | None:
    if not meta:
        return meta
    from .meta import country_name
    return {**meta, "country_name": country_name(meta.get("country", ""))}


def read_track(path: Path, folder: str) -> Track:
    title = artist = ""
    source = None
    track_no = None
    duration = 0.0
    meta = None
    has_cover = False
    try:
        audio = MP3(path)
        duration = audio.info.length
        tags = audio.tags
        if tags is not None:
            if "TIT2" in tags:
                title = str(tags["TIT2"].text[0])
            if "TPE1" in tags:
                artist = str(tags["TPE1"].text[0])
            src = tags.getall(f"TXXX:{SOURCE_TAG}")
            if src:
                source = str(src[0].text[0])
            m = tags.getall(f"TXXX:{META_TAG}")
            if m:
                try:
                    meta = json.loads(str(m[0].text[0]))
                except ValueError:
                    meta = None
            elif "TCON" in tags:  # genre written by another program
                meta = {"genre": str(tags["TCON"].text[0]), "bpm": None, "year": None, "tags": [],
                        "country": "", "cyrillic": False, "partial": True}
            has_cover = bool(tags.getall("APIC"))
            if "TRCK" in tags:
                try:
                    track_no = int(str(tags["TRCK"].text[0]).split("/")[0])
                except ValueError:
                    pass
    except MutagenError:
        pass
    if not title:  # fall back to "Artist - Title.mp3"
        a, t = split_artist_title(path.stem)
        title, artist = t, artist or a
    st = path.stat()
    return Track(path, folder, title, artist, duration, st.st_size, source, track_no, st.st_mtime, meta, has_cover)


def write_meta(path: Path, meta: dict) -> None:
    """Store genre / tempo / year / styles in the MP3 (also as standard tags other players show)."""
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    _set_meta(tags, meta)
    tags.save(path, v2_version=3)


def _set_meta(tags, meta: dict) -> None:
    meta = {k: v for k, v in meta.items() if k not in ("partial", "country_name")}
    tags.delall(f"TXXX:{META_TAG}")
    tags.add(TXXX(encoding=3, desc=META_TAG, text=json.dumps(meta, ensure_ascii=False)))
    if meta.get("genre"):
        tags.setall("TCON", [TCON(encoding=3, text=meta["genre"])])
    if meta.get("bpm"):
        tags.setall("TBPM", [TBPM(encoding=3, text=str(int(meta["bpm"])))])
    if meta.get("year"):
        tags.setall("TDRC", [TDRC(encoding=3, text=str(meta["year"]))])


def write_cover(path: Path, image: bytes, mime: str = "image/jpeg") -> None:
    """Embed the album cover in the MP3."""
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    tags.delall("APIC")
    tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=image))
    tags.save(path, v2_version=3)


def read_cover(path: Path) -> tuple[bytes, str] | None:
    try:
        pics = ID3(path).getall("APIC")
    except (ID3NoHeaderError, MutagenError, OSError):
        return None
    if not pics:
        return None
    pic = next((p for p in pics if p.type == 3), pics[0])
    return pic.data, pic.mime or "image/jpeg"


def write_tags(path: Path, *, title: str, artist: str, album: str,
               source: str | None = None, track_no: int | None = None, meta: dict | None = None) -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    tags.setall("TIT2", [TIT2(encoding=3, text=title)])
    tags.setall("TPE1", [TPE1(encoding=3, text=artist)])
    tags.setall("TALB", [TALB(encoding=3, text=album)])
    if track_no is not None:
        tags.setall("TRCK", [TRCK(encoding=3, text=str(track_no))])
    if source:
        tags.delall(f"TXXX:{SOURCE_TAG}")
        tags.add(TXXX(encoding=3, desc=SOURCE_TAG, text=source))
    if meta:
        _set_meta(tags, meta)
    tags.save(path, v2_version=3)  # ID3v2.3 = widest player compatibility


class LibraryError(Exception):
    pass


class Library:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root).expanduser() if root else default_library_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self._cache: dict[Path, tuple[float, int, Track]] = {}

    # ---------- settings ----------
    @property
    def config_path(self) -> Path:
        return self.root / CONFIG_FILE

    def config(self) -> dict:
        cfg = dict(DEFAULT_CONFIG)
        if self.config_path.exists():
            try:
                cfg.update(json.loads(self.config_path.read_text()))
            except json.JSONDecodeError:
                pass
        return cfg

    def set_config(self, **updates) -> dict:
        cfg = self.config()
        for k, v in updates.items():
            if k not in DEFAULT_CONFIG:
                raise LibraryError(f"Unknown setting '{k}'. Valid: {', '.join(DEFAULT_CONFIG)}")
            default = DEFAULT_CONFIG[k]
            if isinstance(default, bool) and isinstance(v, str):
                v = v.lower() in ("1", "true", "yes", "on")
            elif isinstance(default, int) and not isinstance(default, bool) and isinstance(v, str):
                v = float(v)
                v = int(v) if v.is_integer() else v
            if k == "dupe_scope" and v not in ("library", "folder"):
                raise LibraryError("dupe_scope must be 'library' or 'folder'")
            cfg[k] = v
        self.config_path.write_text(json.dumps(cfg, indent=2))
        return cfg

    # ---------- paths ----------
    def folder_path(self, name: str) -> Path:
        if not name or name != safe_name(name) or name.startswith("."):
            raise LibraryError(msg("err.bad_folder", "Invalid folder name: {name}", name=name))
        return self.root / name

    def resolve_track(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if self.root.resolve() not in p.parents or p.suffix.lower() != ".mp3" or not p.is_file():
            raise LibraryError(msg("err.no_song", "No such song: {song}", song=rel))
        if p.parent.name.startswith("."):
            raise LibraryError(msg("err.no_song", "No such song: {song}", song=rel))
        return p

    # ---------- reading ----------
    def folders(self) -> list[str]:
        return sorted(
            (d.name for d in self.root.iterdir() if d.is_dir() and not d.name.startswith(".")),
            key=str.lower,
        )

    def tracks(self, folder: str | None = None) -> list[Track]:
        names = [folder] if folder else self.folders()
        out: list[Track] = []
        for name in names:
            d = self.folder_path(name)
            if not d.is_dir():
                raise LibraryError(msg("err.no_folder", "No such folder: {name}", name=name))
            files = [f for f in d.iterdir() if f.suffix.lower() == ".mp3" and not f.name.startswith(".")]
            for f in files:
                st = f.stat()
                cached = self._cache.get(f)
                if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
                    out.append(cached[2])
                else:
                    t = read_track(f, name)
                    self._cache[f] = (st.st_mtime, st.st_size, t)
                    out.append(t)
        return sorted(out, key=lambda t: (t.folder.lower(), t.track_no or 10**6, t.filename.lower()))

    # ---------- folder pictures ----------
    def folder_cover(self, name: str) -> Path | None:
        p = self.folder_path(name) / FOLDER_COVER
        return p if p.is_file() else None

    def set_folder_cover(self, name: str, jpeg: bytes | None) -> None:
        d = self.folder_path(name)
        if not d.is_dir():
            raise LibraryError(msg("err.no_folder", "No such folder: {name}", name=name))
        p = d / FOLDER_COVER
        st = d.stat()     # "last updated" means songs added or removed, not a new picture
        if jpeg:
            p.write_bytes(jpeg)
        else:
            p.unlink(missing_ok=True)
        os.utime(d, ns=(st.st_atime_ns, st.st_mtime_ns))

    def stats(self) -> dict:
        tracks = self.tracks()
        per: dict[str, dict] = {}
        for f in self.folders():
            d = self.folder_path(f)
            cover = d / FOLDER_COVER
            per[f] = {"name": f, "count": 0, "duration": 0.0, "size": 0, "updated": d.stat().st_mtime,
                      "cover": int(cover.stat().st_mtime) if cover.is_file() else 0, "_genres": {}, "_artists": {}, "pics": []}
        for t in tracks:
            s = per[t.folder]
            s["count"] += 1
            s["duration"] += t.duration
            s["size"] += t.size
            if t.has_cover and len(s["pics"]) < 4:
                s["pics"].append(t.path.relative_to(self.root).as_posix())
            if (t.meta or {}).get("genre"):
                s["_genres"][t.meta["genre"]] = s["_genres"].get(t.meta["genre"], 0) + 1
            for a in [x.strip() for x in (t.artist or "").split(",") if x.strip()][:1]:
                s["_artists"][a] = s["_artists"].get(a, 0) + 1
        cfg = self.config()
        for s in per.values():
            s["genres"] = [g for g, _ in sorted(s.pop("_genres").items(), key=lambda kv: -kv[1])[:3]]
            s["artists"] = [a for a, _ in sorted(s.pop("_artists").items(), key=lambda kv: -kv[1])[:3]]
            s["synced"] = (cfg.get("synced") or {}).get(s["name"])
        total_size = sum(s["size"] for s in per.values())
        return {
            "root": str(self.root),
            "folders": list(per.values()),
            "count": len(tracks),
            "duration": sum(s["duration"] for s in per.values()),
            "size": total_size,
            "capacity": int(cfg["capacity_gb"] * 1000**3),  # devices are sold in decimal GB
            "duplicates": len(self.duplicates()),
        }

    # ---------- duplicates ----------
    def duplicates(self, scope: str | None = None) -> list[list[Track]]:
        """Groups of tracks that are the same song (same YouTube video, or same artist+title)."""
        scope = scope or self.config()["dupe_scope"]
        tracks = sorted(self.tracks(), key=lambda t: t.added)  # oldest first = the one to keep
        groups: list[list[Track]] = []
        for t in tracks:
            for g in groups:
                ref = g[0]
                if scope == "folder" and ref.folder != t.folder:
                    continue
                if (t.source and t.source == ref.source) or same_song(t.key, ref.key):
                    g.append(t)
                    break
            else:
                groups.append([t])
        return [g for g in groups if len(g) > 1]

    def find_existing(self, *, artist: str = "", title: str = "", source: str | None = None,
                      folder: str | None = None, scope: str | None = None) -> Track | None:
        """Is this song already in the library? Used before downloading."""
        scope = scope or self.config()["dupe_scope"]
        key = song_key(artist, title)
        for t in self.tracks():
            if scope == "folder" and t.folder != folder:
                continue
            if source and t.source == source:
                return t
            if title and same_song(key, t.key):
                return t
        return None

    # ---------- changing things ----------
    def _trash(self, path: Path) -> Path:
        dest_dir = self.root / TRASH / time.strftime("%Y-%m-%d_%H%M%S")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / (f"{path.parent.name} - {path.name}" if path.is_file() else path.name)
        shutil.move(str(path), str(dest))
        return dest

    def create_folder(self, name: str) -> Path:
        p = self.folder_path(safe_name(name))
        p.mkdir(exist_ok=True)
        return p

    def rename_folder(self, old: str, new: str) -> Path:
        src, dst = self.folder_path(old), self.folder_path(safe_name(new))
        if not src.is_dir():
            raise LibraryError(msg("err.no_folder", "No such folder: {name}", name=old))
        if dst.exists() and dst.resolve() != src.resolve():
            raise LibraryError(msg("err.folder_exists", "Folder already exists: {name}", name=new))
        src.rename(dst)
        for t in self.tracks(dst.name):  # keep album tag in sync with the folder name
            write_tags(t.path, title=t.title, artist=t.artist, album=dst.name, track_no=t.track_no)
        return dst

    def delete_folder(self, name: str, permanent: bool = False) -> None:
        p = self.folder_path(name)
        if not p.is_dir():
            raise LibraryError(msg("err.no_folder", "No such folder: {name}", name=name))
        shutil.rmtree(p) if permanent else self._trash(p)

    def delete_track(self, rel: str, permanent: bool = False) -> None:
        p = self.resolve_track(rel)
        p.unlink() if permanent else self._trash(p)

    def move_track(self, rel: str, folder: str) -> Path:
        src = self.resolve_track(rel)
        dst_dir = self.folder_path(folder)
        if not dst_dir.is_dir():
            raise LibraryError(msg("err.no_folder", "No such folder: {name}", name=folder))
        dst = unique_path(dst_dir / src.name)
        t = read_track(src, src.parent.name)
        shutil.move(str(src), str(dst))
        write_tags(dst, title=t.title, artist=t.artist, album=folder, track_no=self.next_track_no(folder))
        return dst

    def next_track_no(self, folder: str) -> int:
        nums = [t.track_no or 0 for t in self.tracks(folder)]
        return max(nums, default=0) + 1

    def empty_trash(self) -> int:
        trash = self.root / TRASH
        if not trash.exists():
            return 0
        n = sum(1 for _ in trash.rglob("*.mp3"))
        shutil.rmtree(trash)
        return n


def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    i = 2
    while (q := p.with_name(f"{p.stem} ({i}){p.suffix}")).exists():
        i += 1
    return q
