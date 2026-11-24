"""SwimTunes: download music as MP3 for swimming earphones and keep the folders tidy."""
__version__ = "0.10.4"
REPO = "jumalley/swimtunes"

try:  # written by the release build, e.g. "v0.2.14"
    from ._build import TAG
except ImportError:   # started by the SwimTunes.pyw launcher, which knows the version it installed
    import os as _os
    TAG = _os.environ.get("SWIMTUNES_TAG") or "dev"
