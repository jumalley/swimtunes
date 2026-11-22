"""Text helpers: normalising song names so duplicates can be detected, and
building filenames that are safe on the FAT32 filesystem the earphones use."""

from __future__ import annotations

import re
import unicodedata

# Bracketed bits that don't change *which song* it is.
_JUNK_WORDS = (
    r"official|video|audio|lyric|lyrics|visuali[sz]er|hd|hq|4k|1080p|720p|"
    r"remaster|remastered|explicit|clean|music video|m/v|mv|full song|"
    r"color coded|letra|subtitulad[oa]|with lyrics|audio only|out now|"
    r"original mix|album version|single version|radio edit|stereo|mono"
)
_BRACKET_JUNK = re.compile(r"[\(\[\{][^\)\]\}]*\b(?:" + _JUNK_WORDS + r")\b[^\)\]\}]*[\)\]\}]", re.I)
_BRACKET_FEAT = re.compile(r"[\(\[\{]\s*(?:feat\.?|ft\.?|featuring|with|prod\.?)\s[^\)\]\}]*[\)\]\}]", re.I)
_BARE_FEAT = re.compile(r"\s(?:feat\.?|ft\.?|featuring)\s.*$", re.I)
# Spotify style suffixes: "Song - Remastered 2011", "Song - Radio Edit"
_DASH_SUFFIX = re.compile(
    r"\s[-–—]\s(?:\d{4}\s)?(?:remaster(?:ed)?|radio edit|single version|album version|"
    r"original mix|mono|stereo|explicit)(?:\s\d{4})?(?:\sversion)?\s*$",
    re.I,
)
_ARTIST_SPLIT = re.compile(r"\s*(?:,|&|/|;|\bfeat\.?\b|\bft\.?\b|\bfeaturing\b|\sx\s|\sand\s)\s*", re.I)
_CHANNEL_SUFFIX = re.compile(r"(?:\s-\sTopic|VEVO|\s?Official(?:\sChannel)?|\sMusic)$", re.I)
_DASHES = (" - ", " – ", " — ", " -- ", " | ")


def _ascii_fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def _squash(s: str) -> str:
    s = _ascii_fold(s).lower().replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def clean_title(title: str) -> str:
    """Human-readable title with YouTube clutter removed (keeps 'feat.')."""
    t = _BRACKET_JUNK.sub("", title or "")
    t = re.sub(r"\s*\|\s*.*$", "", t)  # "Song | Official Video" -> "Song"
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip(" -–—")


def norm_title(title: str) -> str:
    t = _BRACKET_JUNK.sub(" ", title or "")
    t = _BRACKET_FEAT.sub(" ", t)
    t = _BARE_FEAT.sub("", t)
    t = _DASH_SUFFIX.sub("", t)
    t = re.sub(r"\s*\|\s*.*$", "", t)
    return _squash(t)


def norm_artist(artist: str) -> str:
    """Main artist only, normalised."""
    a = _CHANNEL_SUFFIX.sub("", (artist or "").strip())
    a = _ARTIST_SPLIT.split(a)[0] if a else ""
    a = _squash(a)
    return re.sub(r"^the ", "", a)


def song_key(artist: str, title: str) -> tuple[str, str]:
    return norm_artist(artist), norm_title(title)


def same_song(a: tuple[str, str], b: tuple[str, str]) -> bool:
    """Two keys refer to the same song if titles match and artists are compatible."""
    (aa, at), (ba, bt) = a, b
    if not at or at != bt:
        return False
    if not aa or not ba:  # unknown artist on one side: title match is enough
        return True
    return aa == ba or aa.replace(" ", "") == ba.replace(" ", "")


def split_artist_title(raw: str) -> tuple[str, str]:
    """'Artist - Title' -> ('Artist', 'Title'); returns ('', raw) if no separator."""
    for d in _DASHES:
        if d in raw:
            artist, title = raw.split(d, 1)
            if artist.strip() and title.strip():
                return artist.strip(), title.strip()
    return "", raw.strip()


def channel_artist(channel: str) -> str:
    return _CHANNEL_SUFFIX.sub("", (channel or "").strip()).strip()


_FAT_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(name: str, max_len: int = 120) -> str:
    """A filename/folder name that works on FAT32 (the earphones' filesystem)."""
    n = _FAT_BAD.sub("", name or "")
    n = re.sub(r"\s+", " ", n).strip().rstrip(". ")
    if len(n) > max_len:
        n = n[:max_len].rstrip(". ")
    return n or "untitled"


def fmt_duration(seconds: float | None) -> str:
    s = int(round(seconds or 0))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_size(n: int | float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


_CYR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюяіїєґў",
                ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                 "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya", "i", "yi", "ye", "g", "u"]))


def has_cyrillic(s: str) -> bool:
    return any("Ѐ" <= c <= "ӿ" for c in s or "")


def translit(s: str) -> str:
    """Cyrillic -> Latin, so 'Кино' and 'Kino' can be compared."""
    return "".join(_CYR.get(c, _CYR.get(c.lower(), c)) if "Ѐ" <= c <= "ӿ" else c for c in s or "")


def similar(a: str, b: str) -> float:
    """0..1 similarity of two names, ignoring case, accents, punctuation and alphabet."""
    from difflib import SequenceMatcher
    a, b = _squash(translit(a)), _squash(translit(b))
    if not a or not b:
        return 0.0
    if a == b or a.replace(" ", "") == b.replace(" ", ""):
        return 1.0
    return SequenceMatcher(None, a, b).ratio()
