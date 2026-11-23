# PyInstaller recipe for the double-click app.
#   pyinstaller packaging/swimtunes.spec --noconfirm
# Windows -> dist/SwimTunes.exe (one file)    macOS -> dist/SwimTunes.app    Linux -> dist/SwimTunes
import os
import shutil
import sys

import imageio_ffmpeg
from deno import find_deno_bin
from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
EXT = ".exe" if sys.platform == "win32" else ""

# ffmpeg and deno go into bin/ with fixed names; swimtunes/tools.py looks for them there.
stage = os.path.join(ROOT, "build", "stage")
os.makedirs(stage, exist_ok=True)
for src, name in ((imageio_ffmpeg.get_ffmpeg_exe(), "ffmpeg"), (find_deno_bin(), "deno")):
    dst = os.path.join(stage, name + EXT)
    shutil.copy2(src, dst)
    os.chmod(dst, 0o755)

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[ROOT],
    binaries=[(os.path.join(stage, "ffmpeg" + EXT), "bin"), (os.path.join(stage, "deno" + EXT), "bin")],
    datas=[(os.path.join(ROOT, "swimtunes", "templates"), os.path.join("swimtunes", "templates")),
           (os.path.join(ROOT, "swimtunes", "static"), os.path.join("swimtunes", "static"))],
    hiddenimports=collect_submodules("swimtunes") + (collect_submodules("pystray") if sys.platform in ("win32", "darwin") else []),
    # the helper programs are bundled above; don't pull in their (large) Python packages too
    # (Pillow is kept on Windows and Mac: the clock / menu-bar icon needs it)
    excludes=["imageio_ffmpeg", "deno", "tkinter", "pytest"] + ([] if sys.platform in ("win32", "darwin") else ["PIL"]),
    noarchive=False,
)
pyz = PYZ(a.pure)
icon = os.path.join(SPECPATH, "icon.png")

if sys.platform == "darwin":
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="SwimTunes", console=False, icon=icon)
    coll = COLLECT(exe, a.binaries, a.datas, name="SwimTunes")
    app = BUNDLE(
        coll,
        name="SwimTunes.app",
        icon=icon,
        bundle_identifier="io.github.jumalley.swimtunes",
        info_plist={
            "CFBundleDisplayName": "SwimTunes",
            "CFBundleShortVersionString": os.environ.get("SWIMTUNES_TAG", "0.2.0").lstrip("v"),
            "LSUIElement": True,  # no Dock icon: the app lives in the browser
            "NSHighResolutionCapable": True,
        },
    )
else:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name="SwimTunes",
        console=sys.platform != "win32",  # Windows: no black window; an icon next to the clock instead
        icon=icon if sys.platform == "win32" else None,
        upx=False,
    )
