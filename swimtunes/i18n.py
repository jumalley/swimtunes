"""Messages that the dashboard can translate.

A `Msg` is a normal English string (so the command line and logs keep working), that
also remembers a translation key and its values. The dashboard looks the key up in
static/i18n.js and shows the message in the user's language.
"""

from __future__ import annotations


class Msg(str):
    key: str
    params: dict

    def __new__(cls, key: str, text: str, **params):
        try:
            rendered = text.format(**params)
        except (KeyError, IndexError, ValueError):
            rendered = text
        s = super().__new__(cls, rendered)
        s.key = key
        s.params = {k: (v if isinstance(v, (int, float)) else str(v)) for k, v in params.items()}
        return s


def msg(key: str, text: str, **params) -> Msg:
    return Msg(key, text, **params)


def as_entry(text) -> dict:
    """A log line / error for the dashboard: {"t": English text, "k": key, "p": values}."""
    if isinstance(text, Msg):
        return {"t": str(text), "k": text.key, "p": text.params}
    if isinstance(text, BaseException) and text.args and isinstance(text.args[0], Msg):
        return as_entry(text.args[0])
    return {"t": str(text)}


# Names SwimTunes gives to folders it creates. The dashboard sends translated ones.
DEFAULT_LABELS = {
    "other": "Other",
    "unknown_tempo": "Unknown tempo",
    "easy": "Easy (under 100 BPM)",
    "steady": "Steady (100-120 BPM)",
    "fast": "Fast (120-140 BPM)",
    "sprint": "Sprint (140+ BPM)",
    "unknown_year": "Unknown year",
    "unknown_artist": "Unknown artist",
    "russian_language": "Russian-language",
    "downloads": "Downloads",
    "countries": {},
}


def labels(custom: dict | None = None) -> dict:
    out = dict(DEFAULT_LABELS)
    for k, v in (custom or {}).items():
        if k in out and v:
            out[k] = v
    return out
