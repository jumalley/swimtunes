"""The installable package (what SwimTunes.pyw installs with pip) contains everything:
every Python module and sub-package, the page and its scripts. The apps and the other tests
read the source folder directly, so only this test sees a file missing from the package."""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_package_contains_every_module_and_page_file(tmp_path):
    src = tmp_path / "src"          # a clean copy: a leftover build/ folder would hide missing files
    shutil.copytree(ROOT, src, ignore=shutil.ignore_patterns("build", "dist", "*.egg-info", "__pycache__", ".git",
                                                             "node_modules", ".venv", ".ruff_cache", ".pytest_cache"))
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w", str(tmp_path), str(src)],
                   check=True, capture_output=True)
    wheel = next(tmp_path.glob("swimtunes-*.whl"))
    names = set(zipfile.ZipFile(wheel).namelist())
    pkg = ROOT / "swimtunes"
    needed = [p.relative_to(ROOT).as_posix() for p in pkg.rglob("*")
              if p.is_file() and "__pycache__" not in p.parts and p.name != "_build.py"]
    missing = sorted(n for n in needed if n not in names)
    assert not missing, f"not in the installable package: {missing[:15]}"
