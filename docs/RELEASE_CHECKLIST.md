# Release checklist (about 10 minutes)

Every new version is first published as a **test version**. The automatic tests run in the cloud, which has no
Windows desktop, no Bluetooth and no earphones, so before everyone gets a version, try it for real:

**Setup, once:** in SwimTunes, **Settings → Updates → Versions → Test versions first**. Your copy now gets test
versions as soon as they're built (others don't).

## On your computer (Windows)

- [ ] **It updates.** Start SwimTunes: it installs the test version by itself and restarts. The status bar shows
      the new version.
- [ ] **It starts cleanly.** No error box; the icon next to the clock is there (Windows app), or the small
      "Setting up / Starting" window closes by itself (SwimTunes.pyw).
- [ ] **A download works.** Add music → paste a short playlist (3–5 songs) → Find songs → Download. The songs
      appear in their playlist with a cover.
- [ ] **Settings → Is everything working?** All green.
- [ ] **YouTube sign-in.** Settings → Accounts → **Sign in to YouTube**: a window opens; sign in, close it → *Connected
      (SwimTunes window)*. A private playlist of yours then shows its songs.

## With your earphones

- [ ] **Copy.** Plug the earphones in → Earphones → the folders show their status → **Copy to earphones** →
      *Sync finished*.
- [ ] **Eject.** Click **Eject** → *Safe to unplug* → the drive disappears from the computer.
- [ ] **Play.** Unplug, switch on MP3 mode: the new songs play, in playlist order.
- [ ] **Bluetooth.** Earphones off the charger → Earphones → **Read again**: battery and firmware appear.

## If something is wrong

- **Settings → Other → Copy a problem report**, and send it with what you did.
- **Settings → Updates → Go back to …** returns to the previous version (it won't reinstall the test version by itself).
- Don't promote: everyone else stays on the stable version.

## When everything works

On GitHub: **Actions → Promote a test version → Run workflow** (leave the version empty for the newest test
version). Every copy of SwimTunes updates to it at its next start.
