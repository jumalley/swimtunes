"""Command line interface. Run `swimtunes --help`."""

from __future__ import annotations

import argparse
import sys
import webbrowser

from . import __version__, device
from .library import Library, LibraryError
from .text import fmt_duration, fmt_size


def _table(rows: list[list[str]], right: set[int] = frozenset()) -> str:
    if not rows:
        return ""
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = []
    for n, r in enumerate(rows):
        lines.append("  ".join(c.rjust(w) if i in right else c.ljust(w) for i, (c, w) in enumerate(zip(r, widths))).rstrip())
        if n == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def cmd_download(lib, a):
    from .downloader import Downloader
    d = Downloader(lib, bitrate=a.bitrate, normalize=True if a.normalize else None,
                   allow_dupes=a.allow_dupes, scope=a.scope)
    failed = 0
    for url in a.urls:
        failed += len(d.download(url, a.folder).failed)
    return 1 if failed else 0


def cmd_stats(lib, a):
    if a.folder:
        tracks = lib.tracks(a.folder)
        rows = [["#", "Title", "Artist", "Length", "Size"]]
        rows += [[str(t.track_no or i), t.title, t.artist, fmt_duration(t.duration), fmt_size(t.size)]
                 for i, t in enumerate(tracks, 1)]
        print(_table(rows, {0, 3, 4}))
        print(f"\n{len(tracks)} songs, {fmt_duration(sum(t.duration for t in tracks))}, "
              f"{fmt_size(sum(t.size for t in tracks))}")
        return 0
    s = lib.stats()
    rows = [["Folder", "Songs", "Length", "Size"]]
    rows += [[f["name"], str(f["count"]), fmt_duration(f["duration"]), fmt_size(f["size"])] for f in s["folders"]]
    rows.append(["TOTAL", str(s["count"]), fmt_duration(s["duration"]), fmt_size(s["size"])])
    print(f"Library: {s['root']}\n")
    print(_table(rows, {1, 2, 3}))
    pct = s["size"] / s["capacity"] * 100 if s["capacity"] else 0
    print(f"\nEarphones: {fmt_size(s['size'])} of {lib.config()['capacity_gb']} GB ({pct:.0f}% full)")
    if s["duplicates"]:
        print(f"⚠ {s['duplicates']} duplicate song(s). Run `swimtunes dupes` to see them.")
    return 0


def cmd_dupes(lib, a):
    groups = lib.duplicates(a.scope)
    if not groups:
        print("No duplicates 🎉")
        return 0
    removed = 0
    for g in groups:
        keep, *extra = g
        print(f"{keep.artist + ' - ' if keep.artist else ''}{keep.title}")
        print(f"   keep    {keep.folder}/{keep.filename}")
        for t in extra:
            print(f"   {'removed' if a.fix else 'extra  '} {t.folder}/{t.filename}")
            if a.fix:
                lib.delete_track(t.path.relative_to(lib.root).as_posix())
                removed += 1
    if a.fix:
        print(f"\nMoved {removed} extra copies to the trash ({lib.root / '.trash'}).")
    else:
        print(f"\n{len(groups)} song(s) have duplicates. Run `swimtunes dupes --fix` to keep only the oldest copy.")
    return 0


def cmd_folder(lib, a):
    if a.action == "list":
        for f in lib.folders():
            print(f)
    elif a.action == "create":
        print(f"Created {lib.create_folder(a.name).name}")
    elif a.action == "rename":
        print(f"Renamed to {lib.rename_folder(a.name, a.new_name).name}")
    elif a.action == "delete":
        lib.delete_folder(a.name, permanent=a.permanent)
        print(f"Deleted {a.name}" + ("" if a.permanent else " (moved to trash)"))
    return 0


def cmd_move(lib, a):
    for p in a.songs:
        print(f"→ {lib.move_track(p, a.folder).relative_to(lib.root)}")
    return 0


def cmd_rm(lib, a):
    for p in a.songs:
        lib.delete_track(p, permanent=a.permanent)
        print(f"Deleted {p}")
    return 0


def cmd_sync(lib, a):
    path = a.device or lib.config()["device_path"]
    if not path:
        print("Give the earphones' drive path, e.g. `swimtunes sync /Volumes/SWIM` or `swimtunes sync E:\\`")
        return 2
    lib.set_config(device_path=path)
    device.sync(lib, path, mirror=a.mirror, dry_run=a.dry_run)
    return 0


def cmd_config(lib, a):
    if a.key and a.value is not None:
        lib.set_config(**{a.key: a.value})
    for k, v in lib.config().items():
        print(f"{k} = {v}")
    return 0


def cmd_trash(lib, a):
    print(f"Permanently deleted {lib.empty_trash()} songs from the trash.")
    return 0


def cmd_ui(lib, a):
    from .web import create_app
    url = f"http://localhost:{a.port}"
    print(f"SwimTunes dashboard: {url}   (library: {lib.root})\nPress Ctrl+C to stop.")
    if not a.no_browser:
        webbrowser.open(url)
    create_app(lib).run(host="127.0.0.1", port=a.port, debug=False, threaded=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="swimtunes", description="Download music as MP3 for swimming earphones and manage the folders.")
    p.add_argument("--library", "-L", help="library folder (default: $SWIMTUNES_LIBRARY or ~/Music/SwimTunes)")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("download", aliases=["dl"], help="download YouTube / Spotify links as MP3")
    d.add_argument("urls", nargs="+", help="YouTube video/playlist or Spotify playlist/album/track links")
    d.add_argument("-f", "--folder", help="folder to put songs in (default: the playlist's name)")
    d.add_argument("--allow-dupes", action="store_true", help="download even if the song is already in the library")
    d.add_argument("--scope", choices=["library", "folder"], help="where to look for duplicates")
    d.add_argument("--bitrate", type=int, choices=[128, 192, 256, 320])
    d.add_argument("--normalize", action="store_true", help="even out loudness between songs")
    d.set_defaults(fn=cmd_download)

    s = sub.add_parser("stats", aliases=["ls"], help="show folders with song count, length and size")
    s.add_argument("folder", nargs="?", help="show the songs in this folder")
    s.set_defaults(fn=cmd_stats)

    du = sub.add_parser("dupes", help="find duplicate songs")
    du.add_argument("--fix", action="store_true", help="move extra copies to the trash")
    du.add_argument("--scope", choices=["library", "folder"])
    du.set_defaults(fn=cmd_dupes)

    f = sub.add_parser("folder", help="list / create / rename / delete folders")
    fs = f.add_subparsers(dest="action", required=True)
    fs.add_parser("list")
    fs.add_parser("create").add_argument("name")
    r = fs.add_parser("rename"); r.add_argument("name"); r.add_argument("new_name")
    dl = fs.add_parser("delete"); dl.add_argument("name"); dl.add_argument("--permanent", action="store_true")
    f.set_defaults(fn=cmd_folder)

    m = sub.add_parser("move", help="move songs to another folder (paths like 'Folder/Song.mp3')")
    m.add_argument("songs", nargs="+"); m.add_argument("folder")
    m.set_defaults(fn=cmd_move)

    rm = sub.add_parser("rm", help="delete songs (paths like 'Folder/Song.mp3')")
    rm.add_argument("songs", nargs="+"); rm.add_argument("--permanent", action="store_true")
    rm.set_defaults(fn=cmd_rm)

    sy = sub.add_parser("sync", help="copy the library onto the earphones")
    sy.add_argument("device", nargs="?", help="earphones drive, e.g. /Volumes/SWIM or E:\\")
    sy.add_argument("--mirror", action="store_true", help="also remove folders on the earphones that aren't in the library")
    sy.add_argument("--dry-run", "-n", action="store_true")
    sy.set_defaults(fn=cmd_sync)

    c = sub.add_parser("config", help="show or change settings")
    c.add_argument("key", nargs="?"); c.add_argument("value", nargs="?")
    c.set_defaults(fn=cmd_config)

    sub.add_parser("empty-trash", help="permanently delete trashed songs").set_defaults(fn=cmd_trash)

    u = sub.add_parser("ui", help="open the web dashboard")
    u.add_argument("--port", type=int, default=8765)
    u.add_argument("--no-browser", action="store_true")
    u.set_defaults(fn=cmd_ui)
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return a.fn(Library(a.library), a)
    except (LibraryError, FileNotFoundError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
