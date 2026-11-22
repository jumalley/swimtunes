"""A SwimTunes dashboard with a few sample songs, for the browser smoke test.

    python tests/browser/sample_server.py 8799
Offline: no update checks, no network lookups.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["SWIMTUNES_OFFLINE"] = "1"
os.environ.setdefault("SWIMTUNES_HOME", tempfile.mkdtemp(prefix="swimtunes-home-"))
sys.path.insert(0, str(Path(__file__).parents[1]))        # tests/ (helpers)
sys.path.insert(0, str(Path(__file__).parents[2]))        # the project

from helpers import add_song  # noqa: E402

from swimtunes.library import Library  # noqa: E402
from swimtunes.web import create_app  # noqa: E402

lib = Library(Path(tempfile.mkdtemp(prefix="swimtunes-lib-")))
for folder, songs in {"doomer": [("Molchat Doma", "Sudno"), ("Kino", "Gruppa krovi")],
                      "mood": [("Lana Del Rey", "Video Games"), ("TK", "Unravel")],    # Unravel twice on purpose
                      "anime songs": [("TK", "Unravel")]}.items():
    for artist, title in songs:
        add_song(lib, folder, artist, title, seconds=1)
create_app(lib).run(port=int(sys.argv[1]) if len(sys.argv) > 1 else 8799)
