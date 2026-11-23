# Signing the apps

Unsigned apps trigger *"Windows protected your PC"*, are blocked outright by Windows 11 **Smart App Control**,
and need **Open Anyway** on a Mac. Signing fixes this. The build ([`build-apps.yml`](../.github/workflows/build-apps.yml))
already contains the signing steps: they switch on by themselves as soon as the secrets below exist, and are skipped
until then (the apps are then built unsigned, as today).

Secrets go in GitHub: **Settings → Secrets and variables → Actions → New repository secret**.

## Windows: SignPath Foundation (free for open source)

SignPath Foundation signs open-source projects for free, with a certificate in the name "SignPath Foundation".
SwimTunes meets their conditions: open-source license (MIT), public repository, built by GitHub Actions.

1. Apply at [signpath.org](https://signpath.org) (*Apply for free code signing*). Describe SwimTunes and give the
   repository link. Approval takes a few days to a few weeks.
2. Once accepted, in SignPath:
   - create a **project** with slug `swimtunes`, linked to this GitHub repository;
   - keep the default **artifact configuration** (one Windows program, `.exe`);
   - create a **signing policy** with slug `release-signing`, using the release certificate, allowing the
     `build-apps.yml` workflow on branch `main`;
   - create a **CI user** and copy its **API token**.
3. In GitHub, add the secrets:
   - `SIGNPATH_API_TOKEN`: the CI user's token
   - `SIGNPATH_ORGANIZATION_ID`: your organization ID (shown in SignPath's organization settings)
   (Different slugs? Add repository *variables* `SIGNPATH_PROJECT_SLUG` / `SIGNPATH_POLICY_SLUG`.)
4. Publish a new version. The build log shows *Sign the app (SignPath)* and *Sign the installer*; SignPath may ask
   you to approve the first requests on its website.

Both `SwimTunes.exe` and `SwimTunes-Setup.exe` are signed. One limit: the helper programs bundled inside
(ffmpeg) aren't SwimTunes' own code and are not re-signed; if Smart App Control ever blocks one of them,
`SwimTunes.pyw` remains the fallback.

**Alternative (paid, your own name):** Microsoft *Artifact Signing* (formerly Trusted Signing), about $10/month,
with an identity check. Ask to switch the build to it if you prefer.

## Mac: Apple Developer ID (99 $/year)

1. Join the [Apple Developer Program](https://developer.apple.com/programs/).
2. In Xcode or on developer.apple.com, create a **Developer ID Application** certificate. Export it from Keychain
   Access as a `.p12` file with a password, then turn it into text: `base64 -i certificate.p12 | pbcopy`.
3. Create an **app-specific password** at [account.apple.com](https://account.apple.com) (Sign-In and Security).
4. In GitHub, add the secrets:
   - `APPLE_CERTIFICATE`: the base64 text of the `.p12`
   - `APPLE_CERTIFICATE_PASSWORD`: its password
   - `APPLE_SIGNING_IDENTITY`: e.g. `Developer ID Application: Your Name (TEAMID1234)`
   - `APPLE_ID`: your Apple ID e-mail
   - `APPLE_TEAM_ID`: your 10-character team ID
   - `APPLE_APP_PASSWORD`: the app-specific password
5. Publish a new version: the app is signed, notarized by Apple and "stapled", so it opens without *Open Anyway*.

## After signing works

Remove the *"Windows protected your PC"* / *Open Anyway* steps from the README, and mention it in the release notes.
