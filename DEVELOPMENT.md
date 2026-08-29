# Developing WebJam v0.27.1

> **Current source line:** this guide describes post-release v0.27.1 source.
> The exact v0.27.1 tag is the unsigned/ad-hoc GitHub **Latest** private test
> release; current `master` is post-tag source and is not that package.

> **Release boundary:** only the exact tagged v0.27.1 assets and attached
> `WebJam-v0.27.1-SHA256SUMS.txt` manifest are authoritative for the current
> download. Release `377614785` exists, but tag run `33045632613` is red at
> its duplicate-release publisher and is not publish-green. Every v0.27 physical
> result is **NOT RUN**.

## Local setup

Use the repository virtual environment:

```bash
.venv/bin/ruff check webjam_qt/ core/ ui/ services/ api/
.venv/bin/python -m compileall -q core webjam_qt ui services api tests
.venv/bin/python -m pip check
.venv/bin/python ux_smoke_test.py
.venv/bin/pytest -q
```

For Studio arrangement changes, run the focused model, persistence, history,
controller, renderer, comping, waveform, export, and Qt integration modules in
addition to the full suite. The physical-output and external-editor gates in
`TEST_PROCEDURE.md` are separate and currently **NOT RUN** for v0.27.1.

Normal app development starts from Host/Join. Do not make a new startup path
that asks WebJam to choose Jamulus devices, channels, sample rate, buffers, or
jitter settings.

## Pocket Stage developer preview

Pocket Stage is owner-device development work, not a distributed or pre-signed
iOS binary. The v0.22.5 Mac candidates include the generated, CI-compiled
Xcode project and setup kit; physical installation and pairing remain
owner-performed and **NOT RUN**. Run the focused desktop tests with:

```bash
.venv/bin/python -m pytest -q \
  tests/test_pocket_stage.py \
  tests/test_pocket_stage_gateway.py \
  tests/test_pocket_stage_tls.py \
  tests/test_pocket_stage_controller.py
```

On macOS, run the Swift protocol, transport, and deterministic connection-state
tests separately:

```bash
cd ios
swift test
```

The opt-in Python test below launches the real Python WSS gateway and proves the
real Swift `URLSessionWebSocketTask` client can authenticate, pair, receive a
snapshot, and close cleanly:

```bash
WEBJAM_RUN_SWIFT_POCKET_STAGE_INTEGRATION=1 \
  .venv/bin/pytest -q tests/test_pocket_stage_swift_integration.py
```

For an owner-device experiment, follow `ios/README.md`. From a Mac release
package, open the included `Pocket Stage iPhone Setup/WebJamPocketStage.xcodeproj`
directly in Xcode; source developers can generate the same project with the
checked-in generator. Select a unique bundle identifier and the owner's Apple
Personal Team, then install from Xcode. The generated target already contains
the local package and required privacy/network keys. The Pair view uses a
native QR scanner; text injection is only a Simulator/developer aid, not a
physical-user fallback. App Store/TestFlight distribution is not implemented.

Preserve these boundaries in every Pocket Stage change:

- the gateway stays off until **More → Use iPhone as Pocket Stage…** and binds
  only a selected private interface, never wildcard/public or plaintext
  WebSocket;
- the QR capability is one-use, expires in 120 seconds, and pins the exact
  SHA-256 of the ephemeral leaf certificate's DER bytes;
- there is no durable reconnect credential; a disconnected phone needs a fresh
  QR;
- slot display labels are bounded paired-private content and never enter logs,
  diagnostics, support bundles, or the anonymous public Local API;
- commands remain finite, bounded, scope/generation/revision guarded, and
  marshalled to the Qt owner thread;
- there is no phone audio, chat/reactions, solo command, rehearsal plan,
  section/Studio transport, or media path in this slice;
- the Local API and Jamulus audio path remain unchanged, and companion work
  stays off audio/capture/meter/waveform/playback/export callbacks.

Automated tests do not establish installed-device behavior. Physical pairing,
camera and Local Network permission, operating-system firewall allow/deny and
recovery, sleep/wake or IP-change fresh-code recovery, accessibility, correct
mix control, host recording, long-session resources, and audio non-interference
remain **NOT RUN**.

## Dual-musician rehearsal lab

Run the hardware-free source gate when changing host/guest recording,
transfer, Studio, export, or cleanup behavior:

```bash
.venv/bin/python -m pytest -q tests/test_dual_musician_rehearsal_lab.py
QT_QPA_PLATFORM=offscreen PYTEST_ADDOPTS='-p no:cacheprovider' \
  .venv/bin/python -m pytest -q tests/test_multitrack_proof_lab.py
.venv/bin/python tools/run_multitrack_proof_lab.py \
  --report /tmp/webjam-multitrack-proof-lab.json
```

It uses isolated pytest artifacts and synthetic capture only; the separate
Linux/JACK real-Jamulus companion, exact seven-source/20-run matrix, cleanup
limits, and automated-versus-physical evidence boundary are documented in
[Dual-musician rehearsal lab](DUAL_MUSICIAN_REHEARSAL_LAB.md).

## Integration rules

- Launch Jamulus directly and visibly; do not use `--nogui` for the participant
  client.
- Use the supported dedicated `--inifile WebJam-native-v0.16.ini` contract.
- Never write that profile’s content or the user's normal `Jamulus.ini`.
- Do not automate Jamulus through screen coordinates, pixel inspection, or
  window-text scraping.
- JSON-RPC is for process, authentication, roster, connection, chat, and
  recorder facts—not device configuration.
- Keep meeting services external and truthful: the generic boundary accepts
  only hardened public HTTPS DNS-host links, and opening one is not a
  joined/muted or provider-verification claim. Keep native publisher,
  installer, mute-guidance, and bring-forward work Webex-specific. Fully
  redact unknown-provider URLs and hostnames from logs and support mappings.
- Do not add a direct or automatic meeting-app, browser, or system-output
  capture path. Local Originals remain explicitly selected input devices; the
  UI and docs must warn users not to route meeting or system-output audio into
  those inputs. Meeting-service recording stays externally owned.
- Treat `CreatorProfile.capabilities` as an enforced controller boundary, not
  decorative copy. Music and Podcast & Voice are GA. Review & Rehearsal is
  Preview: live session recording and read-only completed-take playback are
  allowed; standalone projects, take editing/comp/mix mutation, track export,
  shared notes, visual sync, and media timecode are blocked.
- Keep local scratchpads profile-scoped and local-only. Fixed mode-0600 files,
  regular-file/no-follow reads, and the 1 MiB ceiling must survive refactors;
  no session synchronization or media-timecode semantics may be inferred.
- Keep Local Originals behind explicit Recording Setup and Studio output in
  Studio.
- Preserve logical mono/stereo identity from capture through recovery, take
  loading, Studio, and export. One stereo map row is one two-channel PCM-24
  file, not two named mono stems.
- Keep Shared Track on the existing separately owned `WebJam Track` Jamulus
  participant. Do not add distributed local playback or a second transport.
- Treat **Record Session** as one creator action but keep recorder, Shared
  Track route, Local Original, transfer, validation, and publication ownership
  independently proved.
- Freeze and recheck the exact take plan: roster/server stem IDs, Shared Track
  fingerprint/playback generation, host topology, guest count/map obligations,
  storage verdict, and expected source count. Never accept substitution,
  under/over-delivery, or reconnect topology drift.
- Never derive guest transport or audibility from a roster row. A bounded
  channel-presence observation is not playing, synchronized, isolated, or
  healthy evidence.

## Guidance ownership rules

- Add operational facts to their real owner, then map finite values into
  `SessionConductorFacts`. Do not infer success from a button, process, meter,
  UI label, note, or request.
- `core/session_conductor.py` owns canonical phase and action;
  `core/musician_guidance.py` is the pure shared projection. Do not create a
  renderer-specific lifecycle or next-action table.
- Controllers may use `GuidanceDisplayOverride` only for fixed, bounded setup
  or topology recovery wording. An override cannot replace generation,
  revision, phase, evidence, output, or preservation truth.
- UI surfaces render the shared snapshot and route semantic actions back to an
  owner. They must not start work while rendering.
- Creative Pulse content stays local and cannot mutate conductor facts. No
  cloud model, agent, or SDK belongs in the v0.26 release line.
- Public consumers get only `to_public_dict()` followed by their own strict
  allowlist. Never add free-form copy, notes, names, channel IDs, addresses,
  devices, paths, invitations, credentials, tokens, or raw exceptions.
- Guidance refresh is event-driven and idempotent. Never call it from meter,
  waveform, playhead, animation, audio, capture, or playback timers/callbacks.
- Add pure phase/action/output tests, cross-surface consistency tests, public
  sanitization tests, an accessibility/no-churn assertion, and 760×600 coverage
  for any new meaningful guidance state.

## UI rules

Use black, white, neutral gray, and burnt orange only. The native three-loop
brand mark lives in `webjam_qt/theme/brand.py`; regenerate `.icns` and `.ico`
with:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m webjam_qt.theme.generate_brand_icons
```

The normal session surface has one dominant next action. Avoid adding device
forms, server fields, or technical diagnostics to Host/Join.

## Build and release hygiene

The source tree's package identity remains `0.27.1`, but current `master` is
post-tag source and is not the published v0.27.1 package. GitHub Latest is the
exact annotated v0.27.1 tag and its eight-asset release.
Do not change that tag, release, or assets, and never create a publisher with
guessed tag objects, commits, CI runs, release IDs, body digests, inventory
digests, asset IDs, sizes, or hashes. After an annotated `v0.27.1` tag and
unique successful tag CI, publish that unsigned/ad-hoc draft as Latest.
Do not invent a signed catalog; sealed v3 still authorizes 0.22.5 only.
Windows remains unsigned and macOS remains ad-hoc signed and unnotarized.

The v0.24.0 tag, asset inventory, checksums, tag CI, and protected promotion
remain immutable historical release evidence and must not be reused.
Published v0.20.0, v0.21.0, v0.22.0, v0.22.1, v0.22.2, v0.22.3, v0.22.4,
and v0.22.5 tags and assets remain immutable historical evidence and must never
be overwritten or served under a moved tag.

The v0.22.5 locks select `cryptography` 50.0.0 for the three audited CVE
remediations. Windows, Linux, and Apple-silicon macOS install exact upstream
wheels. Intel macOS alone uses the reviewed, hash-locked native x86_64
source-build helper with static OpenSSL 3.5.7 LTS because upstream removed the
wheel for that target. Do not generalize that exception or replace it with an
ambient compiler, OpenSSL, network resolver, or unverified wheel.

The local source-bundle smoke command is:

```bash
.venv/bin/python -m PyInstaller --clean --noconfirm webjam.spec
```

This command alone is not release-package evidence. The authoritative native
builders are the four-target `build-desktop` jobs with their exact hashed
locks, staged Jamulus payloads, transport checks, and fresh package launch
smokes in `.github/workflows/ci.yml`. Use the macOS staging/signing/transport
verification there, and do not use the retired `build_webjam.py` release path.
Package and visual verification are required before replacing the installed
test-night app. The cross-platform support boundary, automated gates, and
physical-hardware release checklist are in [Desktop release
runbook](docs/DESKTOP_RELEASE_RUNBOOK.md).
