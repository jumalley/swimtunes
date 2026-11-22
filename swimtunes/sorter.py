"""Decide which folder (playlist) each song goes into."""

from __future__ import annotations

import re
from collections import Counter

from .meta import country_name, decade_label, facts, tempo_label
from .text import safe_name

MODES = {
    "one": "All in one folder",
    "rules": "My folder rules",
    "genre": "By genre (Pop, Rock, Rap…)",
    "style": "By style (post-punk, house, synth-pop…)",
    "tempo": "By swim tempo (easy / steady / fast / sprint)",
    "decade": "By decade (80s, 90s, 2000s…)",
    "country": "By artist's country",
    "artist": "By artist",
}
OTHER = "Other"


def main_artist(artist: str) -> str:
    return re.split(r"\s*(?:,|&| feat\.? | ft\.? | x )\s*", artist or "", maxsplit=1)[0].strip()


def match_rule(rules: list[dict], meta: dict | None, artist: str = "") -> str | None:
    """The rule whose keywords match the song best (most keywords matched; on a tie, the
    earlier rule). Short keywords (country codes like 'ru') must match exactly; longer ones
    may be part of a style ('post-punk' matches 'russian post-punk')."""
    known = facts(meta or {}, artist)
    best, best_score = None, 0
    for rule in rules:
        score = 0
        for kw in rule.get("keywords", []):
            k = kw.strip().lower()
            if k and (any(k == f for f in known) or (len(k) > 3 and any(k in f for f in known))):
                score += 1
        if score > best_score:
            best, best_score = rule["folder"], score
    return best


def destination(mode: str, artist: str, meta: dict | None, *, folder: str = "", rules: list[dict] | None = None) -> str:
    m = meta or {}
    if mode == "rules":
        dest = match_rule(rules or [], m, artist) or folder or OTHER
    elif mode == "genre":
        dest = m.get("genre") or OTHER
    elif mode == "style":
        dest = (m.get("tags") or [m.get("genre") or OTHER])[0].title()
    elif mode == "tempo":
        dest = tempo_label(m.get("bpm"))
    elif mode == "decade":
        dest = decade_label(m.get("year"))
    elif mode == "country":
        dest = country_name(m.get("country", "")) or ("Russian-language" if m.get("cyrillic") else OTHER)
    elif mode == "artist":
        dest = main_artist(artist) or "Unknown artist"
    else:
        dest = folder or "Downloads"
    return safe_name(dest.replace("/", " & "))


def suggest_rules(tracks) -> list[dict]:
    """Learn rules from folders the user already made: what do the songs in each folder
    have in common that songs elsewhere don't?"""
    by_folder: dict[str, list] = {}
    for t in tracks:
        if t.meta:
            by_folder.setdefault(t.folder, []).append(t)
    all_counts: Counter = Counter()
    folder_counts: dict[str, Counter] = {}
    for f, ts in by_folder.items():
        c = Counter()
        for t in ts:
            c.update(set(_rule_facts(t.meta)))
        folder_counts[f] = c
        all_counts.update(c)
    total = sum(len(ts) for ts in by_folder.values())
    out = []
    for f, ts in by_folder.items():
        n = len(ts)
        if n < 2:
            continue
        others = total - n
        scored = []
        for fact, cnt in folder_counts[f].items():
            here = cnt / n
            elsewhere = (all_counts[fact] - cnt) / others if others else 0
            if here >= 0.5 and here - elsewhere >= 0.3:
                scored.append((here - elsewhere, here, fact))
        scored.sort(reverse=True)
        keywords = [fact for _, _, fact in scored[:4]]
        if keywords:
            covered = sum(1 for t in ts if match_rule([{"folder": f, "keywords": keywords}], t.meta, t.artist))
            out.append({"folder": f, "keywords": keywords, "covers": f"{covered} of {n} songs"})
    return sorted(out, key=lambda r: r["folder"].lower())


def _rule_facts(meta: dict) -> list[str]:
    out = [meta.get("genre", ""), *meta.get("tags", [])]
    if meta.get("country"):
        out.append(country_name(meta["country"]))
    if meta.get("cyrillic"):
        out.append("cyrillic")
    return [x.lower() for x in out if x]


def fill_meta(lib, tracks, *, log=print, progress=None, measure: bool = True) -> int:
    """Look up genre / style / country (and measure tempo) for songs that don't have it yet,
    and save it in the MP3s. Returns how many songs were updated."""
    from . import tools
    from .library import write_meta
    from .meta import MetaLookup, estimate_bpm
    lookup = MetaLookup(lib.root / ".swimtunes-meta.json")
    ffmpeg = tools.ffmpeg_path()
    todo = [t for t in tracks if not t.meta or t.meta.get("partial")]
    if todo:
        log(f"Looking up genre, style and tempo for {len(todo)} song(s)…")
    for i, t in enumerate(todo, 1):
        try:
            info = lookup.lookup(t.artist, t.title)
            if not info.get("genre") and t.meta and t.meta.get("genre"):
                info["genre"] = t.meta["genre"]  # keep a genre another program wrote
            if not info.get("bpm") and measure and ffmpeg:
                if bpm := estimate_bpm(t.path, ffmpeg):
                    info["bpm"], info["bpm_measured"] = bpm, True
            write_meta(t.path, info)
            t.meta = info
        except Exception as e:
            log(f"  couldn't look up {t.artist} - {t.title}: {e}")
        if progress:
            progress(i, len(todo))
        if i % 10 == 0:
            lookup.save()
    lookup.save()
    return len(todo)
