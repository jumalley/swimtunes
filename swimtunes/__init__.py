"""SwimTunes: download music as MP3 for swimming earphones and keep the folders tidy."""
__version__ = "0.8.4"
REPO = "jumalley/swimtunes"

try:  # written by the release build, e.g. "v0.2.14"
    from ._build import TAG
except ImportError:
    TAG = "dev"
