# WebJam v0.22.4 source and physical test procedure

> The source tree and immutable GitHub **Latest** release report v0.22.4 as an
> unsigned private test candidate. Its exact eight-asset release and signed
> sequence-5 component catalog passed automated publication gates. Physical
> Pocket Stage installation/pairing, standalone Reference Studio audio,
> external Webex behavior, and Reference Track routing remain **NOT RUN**.

## Scope

This procedure distinguishes automated source/package evidence from physical
musician evidence. A passing source suite does not certify two-Mac audibility,
hardware changes, sleep/wake, interruption recovery, or external-editor import.

The v0.22.4 dependency gate pins `cryptography` 50.0.0 for
CVE-2026-69247, CVE-2026-69248, and CVE-2026-69249. Windows, Linux, and
Apple-silicon macOS use exact upstream wheels. Intel macOS uses only the
documented, hash-locked native x86_64 source-build exception with static
OpenSSL 3.5.7 LTS; its official inputs, architecture, linkage, runtime paths,
license evidence, and final frozen package must all verify.

## Source gate

Run from the repository root:

```bash
.venv/bin/ruff check webjam_qt/ core/ ui/ services/ api/
.venv/bin/python -m compileall -q core webjam_qt ui services api tests
.venv/bin/python -m pip check
git diff --check
git diff --check origin/master...HEAD
.venv/bin/python -m pip_audit --progress-spinner off
.venv/bin/python ux_smoke_test.py
QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q
```

Audit the four native release locks separately. Windows/Linux use the exact
lock with no dependency resolution; the narrowly documented macOS ignore is
for sdist creation while native release jobs install checksum-pinned wheels:

```bash
.venv/bin/python -m pip_audit --progress-spinner off --disable-pip --no-deps -r requirements-lock/windows-x64.txt
.venv/bin/python -m pip_audit --progress-spinner off --disable-pip --no-deps -r requirements-lock/linux-x64.txt
.venv/bin/python -m pip_audit --progress-spinner off --disable-pip --no-deps --ignore-vuln PYSEC-2026-3447 -r requirements-lock/macos-x64.txt
.venv/bin/python -m pip_audit --progress-spinner off --disable-pip --no-deps --ignore-vuln PYSEC-2026-3447 -r requirements-lock/macos-arm64.txt
```

The GitHub `build-desktop` matrix is the authoritative native package gate.
A local PyInstaller bundle is useful smoke evidence only when its Python,
setuptools, hashed release lock, staged Jamulus payload, and transport match
the target job; it is not a substitute for fresh native matrix evidence.

The transport and reference-service jobs are separate from the root pytest
collection. Reproduce their practical local portions with:

```bash
(cd transport && make check)
(cd transport && go test -race -count=1 ./...)
(cd transport && go mod verify && go mod tidy -diff)
(cd transport && go run golang.org/x/vuln/cmd/govulncheck@v1.6.0 ./...)
(cd transport && make build-all VERSION="$(git rev-parse HEAD)")
.venv/bin/ruff check reference_service
.venv/bin/python -m compileall -q reference_service/webjam_reference reference_service/tests
PYTHONPATH=reference_service .venv/bin/python -m pytest -q reference_service/tests
.venv/bin/python -m build reference_service
```

Record local tool-version differences. GitHub CI pins Go and Python versions,
runs the Linux/JACK real-Jamulus path, health-checks the restricted reference
container, and remains authoritative for those platform-specific gates.

Review at minimum:

- One accepted conductor snapshot drives HUD, passive stage, Session Canvas,
  recording/Studio feedback, diagnostics, and Companion guidance without
  contradictory phase or action copy.
- Host, guest, and practice roles cover setup, Band Check, invite/join, live,
  record/stop/validation, guest media, Studio, export, reconnect, cleanup,
  final failure, and indeterminate restart recovery.
- Notes and Creative Pulse cannot change operational phase/action/output facts
  and never enter diagnostics or Companion output.
- Guidance generation/revision guards reject stale observations; identical
  semantic snapshots do not repeat accessibility updates.
- Meter, waveform, playhead, animation, audio, capture, and playback callbacks
  do not derive or announce guidance.
- Companion participants are anonymous slots. Diagnostics/public errors contain
  no names, channel IDs, invitations, addresses, devices, paths, tokens,
  authored content, or raw exceptions.
- Host starts a private server before launching the client.
- Guest launches native Jamulus from one parsed invite.
- The normal launch has no WebJam input/output form or Band Check gate.
- Jamulus receives filename-only `--inifile WebJam-native-v0.16.ini`, no
  `--nogui`, and WebJam writes no profile content.
- Connection proof requires owned process, authenticated RPC, intended path,
  and exactly one local participant; human audibility remains explicit.
- Restart recovery fails closed when profile truth changes.
- Slow manual Jamulus launch remains the active generation; the reconnect
  supervisor cannot cancel it or clear its native profile before that launch
  completes.
- Restart recovery handles stalled but alive Jamulus processes by force-restarting
  the process after repeated heartbeat timeout, then continuing regular auto-reconnect.
- Webex is optional and a failure does not stop music.
- Native Webex detection reports only bounded installation/version/publisher
  state; explicit installation opens an approved Cisco HTTPS URL and never
  stores credentials, downloads/executes a package silently, or changes
  Jamulus audio.
- Direct **Webex Controls** and **More → Webex Controls** reveal the same panel
  without URL handoff. Repeated navigation, Settings changes, Studio return,
  and **Show Webex App** never hand off a meeting URL; only **Join / Open
  Meeting** hands off one validated URL per explicit click. Webex may restore
  whichever of its own windows or screens it already owns.
- On macOS, **Show Webex App** re-verifies any pre-running Cisco PID, validates
  a retained Core Foundation file-reference URL against Cisco's designated
  requirement, and passes that same bound object directly to `NSWorkspace`.
  If stopped, this launches the app itself with no URL or document argument.
  Fresh post-action snapshots must prove exact object identity, PID, publisher,
  and foreground state; request acceptance alone is not success. Webex chooses
  its own screen. Windows/Linux native activation remains disabled without
  publisher proof.
- **Mute in Webex** shows the verified external app for its own control and
  never sends a blind shortcut, reports mute success, or changes Jamulus.
- The Jamulus updater verifies the catalog signature, exact WebJam version,
  expiry, monotonic sequence, target/architecture/roles/capabilities, HTTPS
  origin/redirects, artifact size/hash, and platform publisher/inventory.
- Interrupted/offline/tampered downloads, replay/rollback/equivocation,
  traversal/symlinks, corrupt pointers, concurrent instances, and failed
  activation preserve the current/previous or embedded Jamulus fallback.
- Download may occur while live, but install/activation/rollback refuses every
  client/server/Reference Track/recording/practice/reconnect/launch busy state.
- Windows discloses and proves the known unsigned upstream installer before
  explicit handoff; Linux uses no shell or hidden `sudo`; macOS mounts no DMG
  until the exact license receives explicit acceptance, then verifies upstream
  Developer ID/notarization and preserves quarantine.
- The one Jamulus-name validator covers settings, onboarding, configuration and
  environment recovery, profile, launch, RPC, and legacy paths at 8/9/16/17
  UTF-16-unit and Unicode boundaries; the accessible 8+8 preview matches the
  native mixer behavior.
- Diagnostics and the saved Support Bundle include bounded updater/catalog/
  fallback, Webex-app, and Reference Track source/route facts without paths,
  source names, URLs, meeting links, musician names, tokens, credentials, or
  raw exceptions.
- First Record offers shared-only versus Local Originals.
- Studio output appears only in Studio review.
- Direct **Studio** and Cmd/Ctrl+3 reuse the existing live-take/offline-project
  route; Studio is intentionally absent from More. Direct **Reference Track**
  is host-only and routes to the same panel as More → Reference Track.
- Reference Track source validation accepts real WAV/WAVE, AIFF, and FLAC;
  advertises MP3 only when the packaged decoder proves support; and safely
  rejects renamed/malformed files, symlinks, unsupported channels, oversized
  input, a source that cannot decode its first bounded audio block, and stale
  async completion without exposing paths.
- A host may load and inspect a valid source while route certification is
  unavailable. Source/route status stays independent, Recheck Route starts no
  playback, and BlackHole setup or **Recheck Route** cannot unlock downloaded
  v0.22.2. In current source, production Play becomes eligible only when an
  official 48-kHz BlackHole 16ch/64ch route passes machine-derived
  certification; startup still requires exact live isolation evidence and
  fails closed on uncertainty. Do not record that result as physical
  audibility.
- Controlled-pilot lifecycle tests prove unique descriptor-pinned profile and
  secret ownership, one global WebJam 16ch/64ch claim inherited by the backing
  child, exact live primary/backing CoreAudio routes, retryable startup
  cleanup, and Close serialization. No machine test is recorded as physical
  audibility, independent-mix, direct-monitor, or stem evidence.
- Studio Arrange edits, take-lane comps, undo/redo, save/reopen, and autosave
  failure/retry never change the take manifest or source WAV bytes.
- Standalone Reference Studio create/import/save/reopen/Save As, local
  playback, recording publication/recovery, mixer/automation, tempo review,
  and WAV/FLAC bounce remain isolated from the Jamulus session lifecycle and
  never edit imported source bytes.
- Named-section drag ripple-reorders every track as one revision/undo, preserves
  affine source mapping through seam splits, moves contained arrangement
  metadata, reloads playback, and fails atomically for unsafe seam crossings.
- Waveform work is viewport-bounded, progressive, gap-aware, identity-checked,
  and cancelled when its generation becomes stale.
- The deterministic sparse 12-track/60-minute workspace gate passes
  load/edit/save/reopen, Arrange viewport/zoom, bounded waveform scheduling,
  cancelled export cleanup, and unchanged source hashes without treating that
  fixture as a real session.
- Enabled cycle ranges loop on exact frames across output-block boundaries.
  Cycles of four frames or more apply deterministic seam smoothing across
  short and multi-wrap blocks; one- through three-frame pathological cycles
  remain sample-exact and non-silent and may retain a raw seam. Physical
  click-free loop playback remains a separate **NOT RUN** gate.
- Studio checksum/reader preparation is cancellable background work; a
  generation test rejects stale completion, and callback tests allow no
  pathname open/stat/fstat work.
- Playback and export use the same arranged source catalog and render rules.
- On macOS/Linux with the required secure directory APIs, Studio export is an
  atomic, descriptor-relative, equal-length 24-bit package containing edited
  and original stems, rough mix, markers, import instructions, exact
  arrangement, source manifests, provenance, and SHA-256 checksums.
- On Windows or another unsupported runtime, the button must read **Export
  Aligned Originals**. Its reference mix may apply current trim, fader, pan,
  mute, and solo, but arrangement edits, fades, comps, sections, master
  processing, and attached/repeated take lanes must be explicitly excluded.
  A failed edited Studio export must not silently enter this fallback.
- Export fails closed when the saved Studio state, a source, a source take
  manifest, or cross-take identity changes before publication.
- Cancel/End/Leave clean up owned processes safely.

## Package gate

1. Commit the source implementation and record its SHA.
2. Build with PyInstaller from `webjam.spec`.
3. Stage checksum-verified Jamulus 3.12.2 and JamulusServer 3.12.2 as the
   immutable offline fallback exactly as `.github/workflows/ci.yml` does.
   Separately verify the approved Jamulus 3.12.3 updater inputs without adding
   them to the desktop release inventory or accepting the macOS DMG SLA in CI.
4. Sign nested apps, sidecar, and outer app; verify transport, the required
   absence of `NSAppDataUsageDescription` from every Mac bundle, signatures,
   fresh extraction, and archive SHA-256.
5. Launch the fresh app and inspect Host, Join, Jamulus Updates, direct
   Webex/Track/Studio actions, Webex installed/missing/focus/open behavior,
   Recording Setup, Reference Track load/route separation, Studio
   Arrange/comp/undo/autosave/export, standalone Reference Studio
   project/import/playback/mix/bounce, Support diagnostics, End/Leave, and
   invalid/recovery states at 720×560, 760×600, 1024×768, and 1440×900. On
   macOS/Linux, inspect the edited Studio package. On Windows, verify the
   separately labelled aligned-originals/reference-mix boundary above.
6. Preserve the current rollback package before installing any freshly verified
   candidate app.

## v0.22.4 permissionless macOS Jamulus profile gate

Run this gate against each exact Intel and Apple-silicon DMG and portable ZIP,
not an adjacent source build. Record the package name, SHA-256, app build ID,
Mac model, architecture, and macOS version.

1. Parse the installed outer `WebJam.app/Contents/Info.plist` and each nested
   Jamulus app plist. `NSAppDataUsageDescription` must be absent everywhere.
   Any outer or nested declaration is a package failure, not a reason to grant
   access.
2. Test with no Full Disk Access. Prefer a fresh macOS account or preserve an
   existing denied Other Application Data state so success cannot depend on a
   prior grant. Before starting, record a checksum and metadata for the
   musician's regular `Jamulus.ini` when it exists, plus the initial inventory
   of `~/Library/Application Support/WebJam`.
3. Start Host or Join from the installed package. WebJam must not present an
   Other Application Data prompt. A separate microphone prompt is expected
   when the chosen audio input has not been approved; record it separately.
   The visible musician Jamulus client must open with
   `WebJam-native-v0.16.ini`. On a genuinely fresh profile, complete the
   one-time interface, channel, headphones, and buffer setup, then require the
   normal authenticated connection evidence.
4. Quit WebJam completely, reopen the same installed package, and repeat Host
   or Join. The returning path must use the dedicated Jamulus setup without
   manual profile repair and without an Other Application Data prompt.
5. In that launch, exercise the capability-unlocked controlled Reference Track
   pilot. Its separate Jamulus client must use private profile and control
   files below
   `~/Library/Application Support/WebJam/runtime/reference-track`, must not
   inspect the musician's regular `Jamulus.ini`, and must not trigger an App
   Data prompt.
6. Recheck the regular `Jamulus.ini` checksum and metadata after first setup,
   returning setup, and Reference Track. They must be unchanged. Verify
   WebJam's launch workspace and loopback credentials remain below its own
   Application Support tree with private permissions.
7. If macOS says WebJam wants data from other apps, do not click **Allow** and
   do not add WebJam to Full Disk Access. Record the exact package identity,
   close the setup, and mark this gate **FAIL**.

## Historical v0.16.3 package evidence

- Source: `4d8c04684ee29ab2ea36ae38dc3be8ac6d612c7a` (canonical build source
  for that historical package).
- Release page: https://github.com/rupret007/webjam/releases/tag/v0.16.3
- Installer: `WebJam-v0.16.3-RC-4d8c046-windows-x64-setup.exe`; SHA-256
  `9f1468d2903cbb2648b34d8f98b31d4509ae1f1b9321fe03b0f9424859f266c7`.
- A fresh install and run should pass strict/deep outer and nested
  Jamulus/JamulusServer verification, transport checks, and a frozen Host smoke.
  Keep a rollback package on hand before installing this verified candidate.

## v0.22.4 physical and credentialed ledger

The source suite does not replace this ledger. Every item remains **NOT RUN**
for v0.22.4 until a dated exact asset name, build ID, SHA-256, test environment,
and evidence location are recorded. Draft creation or publication as a private
candidate does not convert any row to PASS.

| Gate | v0.22.4 status |
| --- | --- |
| Two Macs hear each other through physical Jamulus interfaces | **NOT RUN** |
| Host/guest native setup and returning path on both Macs | **NOT RUN** |
| Interface disconnect/reconnect while the session stays truthful | **NOT RUN** |
| Sleep/wake and recoverable Jamulus interruption | **NOT RUN** |
| Shared take, Local Originals, transfer, and host finalization | **NOT RUN** |
| Long recording plus interruption/recovery with source hashes preserved | **NOT RUN** |
| Studio Arrange playback, take-lane audition, and comp through real outputs | **NOT RUN** |
| Reference Studio physical playback, recording, and latency calibration | **NOT RUN** |
| Import edited/original stems, markers, and provenance in an external editor | **NOT RUN** |
| macOS Show Webex App versus Join/Open action separation with real Webex | **NOT RUN** |
| Webex optional behavior without duplicated or interrupted Jamulus music | **NOT RUN** |
| Reference Track two-endpoint audibility, isolation, independent mix/stem, and teardown | **NOT RUN** |
| Pocket Stage physical QR pairing, permissions, control, interruption, and accessibility | **NOT RUN** |
| Signed clean install, quarantine/SmartScreen, trust, and notarization | **NOT RUN** |

Use the dedicated
[Reference Track macOS pilot](docs/plans/webjam-reference-track-macos-pilot.md)
and [Pocket Stage plan](docs/plans/webjam-pocket-stage-v1.md) for those
feature-specific gates.

## v0.22.2 physical and credentialed ledger

The source suite does not replace this ledger. Every item remains **NOT RUN**
for v0.22.2 until a dated exact asset name, build ID, SHA-256, test environment,
and evidence location are recorded. Publishing v0.22.2 as GitHub Latest did not
convert any row to PASS.

| Gate | v0.22.2 status |
| --- | --- |
| Two Macs hear each other through physical Jamulus interfaces | **NOT RUN** |
| Host/guest native setup and returning path on both Macs | **NOT RUN** |
| Interface disconnect/reconnect while the session stays truthful | **NOT RUN** |
| Sleep/wake and recoverable Jamulus interruption | **NOT RUN** |
| Shared take, Local Originals, transfer, and host finalization | **NOT RUN** |
| Long recording plus interruption/recovery with source hashes preserved | **NOT RUN** |
| Studio Arrange playback, take-lane audition, and comp through real outputs | **NOT RUN** |
| Reference Studio physical playback, recording, and latency calibration | **NOT RUN** |
| Import edited/original stems, markers, and provenance in an external editor | **NOT RUN** |
| macOS Show Webex App versus Join/Open action separation with real Webex | **NOT RUN** |
| Webex optional behavior without duplicated or interrupted Jamulus music | **NOT RUN** |
| Reference Track two-endpoint audibility, isolation, independent mix/stem, and teardown | **NOT RUN** |
| Pocket Stage physical QR pairing, permissions, control, interruption, and accessibility | **NOT RUN** |
| Signed clean install, quarantine/SmartScreen, trust, and notarization | **NOT RUN** |

The historical
[v0.18 unified-guidance pilot](V018_UNIFIED_GUIDANCE_PILOT.md) remains useful
for its musician-facing observation format, but record v0.22.2 results here
with the current labels and exact package identity. Use the dedicated
[Reference Track macOS pilot](docs/plans/webjam-reference-track-macos-pilot.md)
and [Pocket Stage plan](docs/plans/webjam-pocket-stage-v1.md) for those
feature-specific gates.
