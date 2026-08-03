# WebJam v0.19.0 — Phase 1: Installation (owner device)

**Device:** Mac mini (2023), Apple M2, 8 GB, macOS Sequoia 15.7.8
**Date:** 2026-07-24
**Candidate:** v0.19.0, tag commit `977b3db95763e521399f03065d75f760f6fb26b4`

## 1.1 Architecture confirmation — PASS
Apple M2 → `arm64`. Correct asset selected:
`WebJam-v0.19.0-macos-arm64-ADHOC-TEST-ONLY.dmg`.

## 1.2 Download + SHA-256 verification — PASS
| Item | Value |
|---|---|
| Size | 298,885,788 bytes (285 MB) |
| Published SHA-256 | `23e4dacc2cd1b42ecfb0bd95077faec10fbe3ca2e7b467ce0898c9b48cc0bf50` |
| Computed SHA-256 | `23e4dacc2cd1b42ecfb0bd95077faec10fbe3ca2e7b467ce0898c9b48cc0bf50` |
| Result | **Match** |

Checksum computed independently of the download path, before the DMG was opened.

## 1.3 DMG inventory — PASS
Mounted as volume `WebJam 0.19.0` with no Gatekeeper prompt on mount. All seven
documented items present:

1. `Applications` (symlink)
2. `Install WebJam - Remove Quarantine.command` (advanced helper)
3. `Install WebJam.command` (guided helper)
4. `Pocket Stage iPhone Setup/`
5. `READ ME FIRST.txt`
6. `WebJam.app`
7. `WebJam Candidate Info.txt`

`WebJam Candidate Info.txt`:
```
format=1
version=0.19.0
build_id=977b3db95763e521399f03065d75f760f6fb26b4
target=macos-arm64
architecture=arm64
trust=ad-hoc-unnotarized
```
Build ID matches the published release commit. **Consistent.**

`Pocket Stage iPhone Setup/` contains `WebJamPocketStage.xcodeproj` (pre-generated),
`project.yml`, `Package.swift`, `Sources/`, `Tests/`, `Fixtures/`,
`Open Pocket Stage in Xcode.command`, and its own `READ ME FIRST.txt`.
`Pocket Stage Build Info.txt` reports `desktop_version=0.19.0`,
`desktop_build_id=977b3db9…`, `distribution=apple-personal-team-owner-device` —
matching the desktop candidate. The v0.19.0 fix ("ship Pocket Stage in Mac
candidate") is verifiably present.

## 1.4 Guided installation — **FAIL (blocker)**

### WJ-001 — Guided installer cannot be launched by any documented path (Severity: High)

**Build:** v0.19.0 macos-arm64 DMG, downloaded via browser (normal user path)
**Component:** `packaging/macos/Install WebJam.command` + `READ ME FIRST.txt` + release notes

**Steps to reproduce**
1. Download `WebJam-v0.19.0-macos-arm64-ADHOC-TEST-ONLY.dmg` with a browser
   (sets `com.apple.quarantine` on the disk image; contents inherit it).
2. Open the DMG.
3. Double-click `Install WebJam.command` — the step `READ ME FIRST.txt` calls
   "Recommended installation" step 1.

**Expected:** Terminal opens and the helper runs its package checks, or macOS
presents an approval path that lets the user proceed.

**Actual:** Gatekeeper blocks the script. macOS shows:

> **"Install WebJam.command" Not Opened**
> Apple could not verify "Install WebJam.command" is free of malware that may
> harm your Mac or compromise your privacy.
> [ Done ] [ Move to Trash ]

The only choices are dismiss or delete. The installer never executes.

**Documented recovery also fails.** `READ ME FIRST.txt` states:
> "If macOS blocks the helper itself, Control-click the helper, choose Open, and
> confirm that you want to open it."

Control-click → Open produces the *same* block dialog. macOS 15 removed the
Control-click→Open bypass for shell scripts; it now applies only to app bundles.

**No "Open Anyway" fallback exists.** After the block, System Settings →
Privacy & Security → Security shows only *Allow applications from*, *FileVault*
and *Lockdown Mode*. No "Open Anyway" row appears, because that mechanism covers
app bundles, not `.command` scripts. Verified directly.

**Chicken-and-egg defect.** The advanced helper
`Install WebJam - Remove Quarantine.command` exists to strip quarantine — but it
is itself a quarantined `.command` on the same disk image, so it is blocked by
exactly the same rule. The escape hatch cannot be reached by the problem it solves.

**Net effect:** on a stock macOS 15 Mac, a user who follows the documented
instructions has **no working path** to install WebJam v0.19.0. Both shipped
helpers are unreachable, and the one documented recovery does not work.

**Notes on what is *not* wrong**
- The exec bit is correct: the copied helper is `-rwx` (mode 755), and CI asserts
  `test -x` on the mounted image. Permissions are not the cause.
- `.command` double-click is **not** broken on this Mac. A control script placed
  outside quarantine later executed normally and left proof:
  `EXECUTED at Sun Jul 26 20:02:46 CDT 2026 / uname: arm64 15.7.8 /
  tty_stdin: yes / TERM_PROGRAM=Apple_Terminal`. This isolates the cause to
  Gatekeeper quarantine specifically.
- The script itself is sound on inspection: `set -euo pipefail`, an EXIT trap that
  pauses so errors stay readable, staged install via `ditto`, signature + build-ID
  + transport-checksum + architecture verification, backup/rollback, and no `sudo`.
  It never gets the chance to run.

**Suggested remedies (for Phase 3)**
1. Document the drag-to-`Applications` path as the primary flow. App bundles *do*
   get an "Open Anyway" row, so the plain DMG flow works where the helpers cannot.
   The `Applications` symlink is already on the image but is never mentioned.
2. Correct `READ ME FIRST.txt`: Control-click → Open no longer works for scripts
   on macOS 13+/15. Replace with a path that actually works.
3. Consider shipping the helper as a minimal `.app` bundle (which can be
   ad-hoc signed and reaches the Open Anyway flow) instead of a bare `.command`.

> **STATUS AT HEAD (0.22.2): ALREADY FIXED — no Phase 3 work required.**
> Remedies 1 and 2 landed in **v0.20.0**. The maintained
> `packaging/macos/READ ME FIRST.txt` now opens with
> *"1. Drag WebJam.app onto the Applications shortcut in this window"*, states
> that *"Recent macOS versions can block downloaded `.command` files without
> offering the same Open Anyway approval, so the installer helpers below are not
> the recommended double-click path"*, and documents running a helper explicitly
> via Terminal (`/bin/bash ` + drag the helper in). CHANGELOG v0.20.0 records the
> same change. **This finding is confirmed valid and confirmed resolved.** It is
> retained here as evidence that the v0.19.0 published asset is not installable
> by its own instructions — relevant only if v0.19.0 is ever re-distributed.
> Remedy 3 (ship the helper as a signed `.app`) remains open and optional.

## 1.5 Installed identity — NOT YET ESTABLISHED
Installation did not complete. Pre-existing state recorded for comparison:

| Field | Value |
|---|---|
| Installed bundle | `/Applications/WebJam.app` |
| Version (Finder Get Info) | **0.18.1** |
| Kind | Application (Apple silicon) |
| Size | 762,600,505 bytes |
| Installed | 2026-07-21 17:09 |

### WJ-002 — Running app reports a different version than the installed bundle (Severity: Medium, unconfirmed)
The WebJam window open at the start of this session was titled
**"WebJam — Band Session (v0.15.0)"** while `/Applications/WebJam.app` reports
version **0.18.1**. Either the running process was launched from a different copy,
or the window title's version string is stale/hardcoded. **To be confirmed** in
Phase 2 against a known-version launch before this is treated as a defect.

## Preserved state
WebJam-only preferences backed up to `audit-v0.19.0/config-backup/` before any
change: `.webjam_app.db`, `.webjam_config.json`, `.webjam_mix.json`,
`.webjam_notes.md`, `.webjam_session.json`, `.webjam-installation.json`.
`.webjam_jsonrpc_secret` was **deliberately not copied** (credential; must not be
placed in a git working tree). Log files were not copied.

### WJ-003 — Config scattered across the home directory (Severity: Low)
WebJam writes 13 dotfiles directly into `~`: `.webjam_app.db`,
`.webjam_config.json`, `.webjam_jamulus.log`, `.webjam_jsonrpc_secret`,
`.webjam_mix.json`, `.webjam_notes.md`, `.webjam_practice_server.log`,
`.webjam_session.json`, `.webjam-installation.json`, `.webjam.log`,
`.webjam.log{.1,.2,.3}`. macOS convention is
`~/Library/Application Support/WebJam/`, which the app already uses for
`takes_directory` and the server RPC secret — so the layout is inconsistent with
itself. Also note ~4.5 MB of rotated logs accumulating in `~`.

## Audit limitation discovered
macOS security dialogs (Gatekeeper, and TCC prompts for Microphone / Local
Network) are drawn by system agents that cannot be added to the automation
allowlist. They are **invisible in automated screenshots and cannot be clicked**.
Any step gated behind such a prompt must be performed by the device owner and is
recorded as USER CONFIRMATION REQUIRED rather than PASS/FAIL.
