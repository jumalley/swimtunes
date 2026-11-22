# Developing SwimTunes

## Run from source

Python 3.11 or newer.

```bash
git clone https://github.com/jumalley/swimtunes.git
cd swimtunes
pip install -e ".[test]"
python -m swimtunes ui
```

ffmpeg and Deno (needed for YouTube) come from PyPI (`imageio-ffmpeg`, `yt-dlp[deno]`), so nothing else is needed.
A `git clone` updates itself from the dashboard (`git pull`, new requirements, restart).

The music folder is `~/Music/SwimTunes` (`--library` or `SWIMTUNES_LIBRARY` to change it). Logins and tokens are
in `~/.swimtunes` (`SWIMTUNES_HOME`). `SWIMTUNES_OFFLINE=1` turns off update checks and online lookups.

## Command line

Everything is in the dashboard; the command line is for scripting and checks:

```bash
python -m swimtunes download "https://www.youtube.com/playlist?list=..." -f "Morning Swim"
python -m swimtunes stats [FOLDER]     # folders / songs with length and size
python -m swimtunes dupes [--fix]      # list duplicates / move extra copies to the trash
python -m swimtunes sync H:\           # copy to the earphones (never deletes songs only on them)
python -m swimtunes doctor             # check ffmpeg, deno and yt-dlp
```

## How the code is organized

| File | What it does |
|---|---|
| `swimtunes/web.py` | the dashboard's server: API, background jobs with live progress, status bar |
| `swimtunes/templates/index.html`, `static/app.js`, `static/app.css` | the dashboard page (no build step, no framework) |
| `swimtunes/static/i18n.js` | the page's texts in 6 languages, one block per language, same keys everywhere |
| `swimtunes/i18n.py` | translatable server messages: `msg("key", "English text", **values)` |
| `swimtunes/downloader.py` | find the songs behind a link, download from YouTube, convert to MP3, save in order |
| `swimtunes/spotify.py`, `accounts.py` | Spotify links (public page or API), YouTube/Spotify logins |
| `swimtunes/meta.py` | genre/tempo/year (Deezer), styles/country (MusicBrainz), tempo measured from the audio |
| `swimtunes/sorter.py` | sorting modes, folder rules, learning rules from existing folders |
| `swimtunes/library.py` | the music folder: tags, covers, stats, duplicates, trash, settings |
| `swimtunes/covers.py` | song covers, playlist pictures and picture suggestions |
| `swimtunes/device.py` | find the earphones, compare, import, copy in play order |
| `swimtunes/shokz.py` | battery / firmware / settings over Bluetooth (RFCOMM; message format from Gadgetbridge) |
| `swimtunes/updater.py` | updates: release download (apps), `git pull` (source), or the launcher |
| `swimtunes/app.py`, `tray.py`, `shortcut.py` | starting the app, the Windows clock icon, desktop shortcut |
| `swimtunes/text.py`, `tools.py` | song title clean-up and matching; finding ffmpeg/deno, health checks |
| `SwimTunes.pyw` | one-file launcher for PCs that block unsigned apps (standard library only) |
| `packaging/` | PyInstaller recipe, Windows installer (Inno Setup), icon |

## Tests

```bash
pytest                      # unit tests (about 10 s); translation checks need Node.js
ruff check .                # mistakes only: undefined/unused names, likely bugs
```

| Tests | Cover |
|---|---|
| `test_core.py` | title matching, library, downloads, sync, the dashboard basics |
| `test_discovery.py` | finding artist/title/genre for hard cases (soundtracks, anime, missing titles) |
| `test_downloads_covers_import.py` | YouTube errors and retries, parallel downloads, covers, importing from the earphones |
| `test_sorting_and_earphones.py` | sorting modes and rules, genre lookups, compare/import/sync |
| `test_live_progress.py` | live progress, stop buttons, translatable messages, status bar |
| `test_accounts.py` | YouTube and Spotify logins (simulated), private playlists |
| `test_playlists.py` | playlist cards: pictures, swims, last update, earphone status |
| `test_bluetooth.py` | the Shokz protocol against simulated earphones |
| `test_updates.py` | automatic updates, the launcher, the clock icon |
| `test_translations.py` | every language complete, placeholders match, every key used exists |

Shared helpers (tiny real MP3s, a pretend YouTube) are in `tests/helpers.py`. Nothing touches the network or your
real music or logins.

**Browser smoke test**: opens every tab on a computer and a phone, in English and French, and fails on any page error,
untranslated text or sideways scrolling:

```bash
python tests/browser/sample_server.py 8799 &
npm install --no-save playwright@1.63.0 && npx playwright install chromium   # once
node tests/browser/smoke.js http://localhost:8799 screenshots/
```

## Continuous integration and releases

- [`tests.yml`](../.github/workflows/tests.yml) runs on every push and pull request: lint and unit tests on Linux
  (Python 3.11 and 3.13), Windows and Mac, plus the browser smoke test.
- [`build-apps.yml`](../.github/workflows/build-apps.yml) builds the Windows app and installer and the Mac apps, checks
  each with `doctor`, and publishes a release:
  - **To release, change `__version__` in `swimtunes/__init__.py`** (and `version` in `pyproject.toml`). The push
    publishes `v<version>`. Pushes that don't change the version only run the tests.
  - Once a month it rebuilds the same version with the newest yt-dlp as `v<version>.<run number>`, so installed copies
    pick up YouTube fixes.

- [`tidy-releases.yml`](../.github/workflows/tidy-releases.yml) keeps the 3 newest releases after each build and
  deletes older ones with their tags.

Installed apps compare versions number by number (`v0.9.0.57` > `v0.9.0`, `v0.9.1` > `v0.9.0.57`).

## Translations

Add a text: add the key to every language block in `static/i18n.js` (the tests check that all six have it, with the same
`{placeholders}`). `"a|b"` is singular|plural, chosen with `{n}`. Add a language: copy the `en` block, translate it,
and add it to `LANG_NAMES`.
