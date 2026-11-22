import os
import tempfile

os.environ["SWIMTUNES_OFFLINE"] = "1"                      # no update checks against GitHub during tests
os.environ["SWIMTUNES_HOME"] = tempfile.mkdtemp(prefix="swimtunes-home-")   # never touch real accounts


import pytest  # noqa: E402

from swimtunes.library import Library  # noqa: E402


@pytest.fixture
def lib(tmp_path):
    return Library(tmp_path / "lib")
