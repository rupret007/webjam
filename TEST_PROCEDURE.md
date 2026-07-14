# WebJam v0.12.0 certification procedure

**Last updated:** 2026-07-14
**Current target:** private Apple Silicon two-Mac v0.12.0 candidate

The current-source product path is:

> Host choice → Confirm sound → Band Check → Invite → Join choice → Confirm sound → Band Check → Play → Record → Studio → Export → End

The exact v0.12.0 package used tonight follows this same flow. It includes the
sound-confirmation screen, CoreAudio route preflight, recording-storage guard,
and private in-progress recording-evidence journal. The v0.11.0 ZIP is a
preserved rollback artifact only.

This procedure separates deterministic source evidence, real Jamulus/JACK
evidence, packaged macOS evidence, and physical musician evidence. Passing one
kind never silently fills another.

## 1. Source gate

Run Qt workflows serially; concurrent offscreen processes can share Qt state
and create false failures.

```bash
.venv/bin/python -m ruff check --no-cache \
  webjam_qt/ core/ ui/ services/ api/ \
  jamulus_controller.py jamulus_state_manager.py
.venv/bin/python -m compileall -q \
  webjam_qt core ui services api \
  jamulus_controller.py jamulus_state_manager.py
.venv/bin/python -m pip check
git diff --check
QT_QPA_PLATFORM=offscreen .venv/bin/python ux_smoke_test.py
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/ -q
```

Record exact counts and failures. An interim focused or full run before all
concurrent edits landed is useful slice evidence, not the final source gate.
The final v0.12.0 source gate recorded **1,687 passed, 18 skipped, one known
Starlette/httpx warning, and 6 subtests**, with no failures or errors.
The final run must include:

- strict v1/v2 invitation parsing and credential redaction;
- Band Check input/output/scratch/host/Studio/support outcomes;
- stable participant identity and authenticated presence generations;
- local capture absolute-frame gaps, writer timeout, recovery, and shutdown;
- recording-folder write probes, conservative free-storage reserves, visible
  nonblocking warnings, and the guarantee that an unsafe Record attempt arms
  neither recorder;
- resumable/idempotent size/SHA/PCM-verified guest transfer;
- schema-v2 missing/partial/damaged/segment/rate/project truth;
- Studio seek/waveform/mixer/output/reopen behavior;
- offset/drift/rate/gap alignment and non-destructive manual restoration;
- atomic Logic exports, independent analysis, source checks, and checksums;
- support preview/saved-ZIP parity, separate sanitized clipboard-summary
  coverage, and adversarial redaction;
- owned-process/port cleanup and fresh-host restart;
- the three-part black/white/burnt-orange brand assets.

### Recording durability coverage included in v0.12.0

Run the focused recording checks before packaging:

```bash
.venv/bin/python -m pytest -q \
  tests/test_recording_readiness.py \
  tests/test_recording_manifest_journal.py \
  tests/test_preflight.py \
  tests/test_band_check.py \
  tests/test_server_rpc_and_record_button.py \
  tests/test_take_project.py \
  tests/test_take_library.py \
  tests/test_take_export.py \
  tests/test_ready_check_ui.py
```

This group covers the writable-folder/storage reserve, private
0600 in-progress evidence journal, start/stop/lifecycle checkpoint integration,
private-name/invite/address/credential redaction, hidden-work-directory take
discovery exclusion, final-manifest retirement, and Logic-export propagation.
The v0.12.0 package contains this code. Package inspection confirms presence;
the physical worksheet remains the only place to credit actual recording,
interruption recovery, audibility, and Logic import.

## 2. Real Jamulus/JACK boundary gate

The opt-in Linux/JACK harness uses real Jamulus 3.12.2 processes. Fixtures enter
through each client's JACK capture ports before encoding and received PCM
leaves its JACK playback ports after decoding. This is stronger than a mocked
RPC test, but it is not macOS/Core Audio or acoustic evidence.

On a prepared Linux host with JACK-Client, numpy, jackd2, jack-tools, and the
official 3.12.2 server/client binaries:

```bash
WEBJAM_JAMULUS_BINARY=/usr/bin/jamulus-headless \
WEBJAM_JAMULUS_CLIENT_BINARY=/usr/bin/jamulus \
WEBJAM_RUN_JACK_AUDIO_INTEGRATION=1 \
  .venv/bin/python -m pytest \
    tests/test_real_jamulus_audio.py \
    tests/test_real_jamulus_recording_pipeline.py -v -s
```

Require two roster identities, distinct 440/660-Hz receive identity, bounded
dropout windows, silence/cross-rejection/peak/rate/channel/frame checks, two
real server stems, project/Studio/export traversal, preserved source hashes,
and zero owned processes/test ports after cleanup.

The short rehearsal exercises the same recorder/reconnect/resource/cleanup
machinery but never counts as longevity certification:

```bash
WEBJAM_JAMULUS_BINARY=/usr/bin/jamulus-headless \
WEBJAM_JAMULUS_CLIENT_BINARY=/usr/bin/jamulus \
WEBJAM_RUN_JACK_AUDIO_SOAK_SMOKE=1 \
WEBJAM_JACK_AUDIO_SMOKE_SECONDS=18 \
WEBJAM_JACK_AUDIO_SMOKE_REPORT=artifacts/jamulus-jack-smoke.json \
  .venv/bin/python -m pytest tests/test_real_jamulus_audio_soak.py \
    -k short_soak_rehearsal -v -s
```

Longevity certification refuses any requested duration below 3,600 seconds:

```bash
WEBJAM_JAMULUS_BINARY=/usr/bin/jamulus-headless \
WEBJAM_JAMULUS_CLIENT_BINARY=/usr/bin/jamulus \
WEBJAM_RUN_JACK_AUDIO_SOAK=1 \
WEBJAM_JACK_AUDIO_SOAK_SECONDS=3600 \
WEBJAM_JACK_AUDIO_SOAK_REPORT=artifacts/jamulus-jack-soak.json \
  .venv/bin/python -m pytest tests/test_real_jamulus_audio_soak.py -v -s
```

Preserve the JSON report. Review actual duration, signal cycles, recorder
cycles/restarts, reconnect result, RSS/CPU/file-descriptor growth, WAV count and
bytes, xrun rate, and the zero-process/port cleanup threshold. A killed,
truncated, short, or `success=false` report fails the gate.

## 3. Exact macOS bundle gate

Build from a clean PyInstaller work directory and add the pinned official
client/server bundles using the existing packaging workflow:

```bash
.venv/bin/python -m PyInstaller --clean --noconfirm webjam.spec
```

Do not overwrite the preserved v0.10.0 or v0.11.0 rollback ZIPs. These values
identify the active private v0.12.0 candidate:

```text
Source/build commit:    796e9a4ddebe79f430b0ded8cf8034bc27836dd0
Artifact filename:      WebJam-v0.12.0-TEST-NIGHT-macos-arm64.zip
Artifact absolute path: /Users/jeffstory/Documents/WebJam 2/WebJam-v0.12.0-TEST-NIGHT-macos-arm64.zip
SHA-256:                01427316820b884d61546d40a9327a49cedf43d6a60a4d88b5b29ab4c693a24c
Architecture:           arm64 (Apple Silicon)
Installed test app:      /Applications/WebJam.app
Rollback artifacts:      preserved v0.11.0 and v0.10.0 test-night packages
```

The bundled official Jamulus 3.12.2 DMG checksum was verified before staging
the nested client and server apps. The exact v0.12.0 archive was fresh-extracted
and verified; it is the only candidate this procedure credits.

Inspect that exact fresh extraction:

- `Info.plist` reports `0.12.0` and registers the `webjam` URL scheme.
- The bundle contains Band Check, private session transfer, schema-v2 project,
  Studio, export, support preview, brand assets, licenses, Inter, the
  confirmation/route/storage/journal path, and official Jamulus/JamulusServer
  3.12.2 resources.
- The app and nested bundles have the intended architecture/version.
- `codesign --verify --deep --strict` succeeds for the complete app.
- The signed arm64 `webjam-fabric` matches its canonical manifest under
  `Contents/Resources`, embeds the exact source commit, and passes bounded
  ready/hello/shutdown IPC through the production validator.
- The package gate passed strict/deep signature verification, nested-app
  inspection, sidecar build/hash/IPC validation, and two isolated six-second
  offscreen launch/TERM cycles.
- Host service ports, live recording, End/quit cleanup with hardware, and a
  fresh host after a real musician run remain physical worksheet checks.

This private candidate may be ad-hoc signed and not notarized. Control-click →
Open or Privacy & Security → Open Anyway is an acceptable first-launch step. A
missing/invalid sealed resource or “damaged app” result fails packaging.

## 4. Candidate-flow smoke

Use an isolated preferences/home profile and the exact extracted v0.12.0 app.

The fresh package completed two isolated six-second offscreen launch/TERM
cycles. That verifies startup and bounded cleanup only; it is not a live-audio,
route, roster, recording, reconnect, or Logic result. Attach the test
interfaces before completing the musician steps below.

1. Launch shows the original three-part WebJam mark and the restrained black,
   white, neutral, and burnt-orange system. No purple or teal remains. After
   Host or Join, the candidate shows the short name-and-sound confirmation;
   **Band input** and **Band output & review** expose the intended CoreAudio
   route without claiming it is audible.
2. Band Check performs explicit input/output/scratch actions, separates its
   local PortAudio evidence from Jamulus send/receive observations, and reports
   one typed outcome. Blank Webex remains optional.
3. Host reaches ready state before exposing Copy Invite. A v2 link is treated
   as a private enrollment credential and never appears in support output. A
   peer-start failure visibly falls back to v1 with guest local-original capture
   and delivery off while preserving join/play and host-side server recording.
4. A valid cold-start link fills and accepts the connection, then proceeds
   through confirmation, Band Check, and **Start Session** before joining. A
   valid already-running deep link is accepted by the same parser and switches only
   after its current-session guard. Malformed/ambiguous links fail safely; a
   legacy v1 link joins without claiming any WebJam guest local capture or the
   private recording plane.
5. Host/guest participant identity remains stable across name/channel/reconnect
   changes. No duplicate musician appears.
6. With local originals explicitly enabled, host capture starts during safe
   preflight before server START; guest capture starts after authenticated host
   recording state. Peer outage does not stop an active local writer, and v2
   verified transfer resumes without deleting the guest original.
7. Studio shows missing/partial/damaged/transferring truth, plays mixed-rate
   multi-segment projects with gaps, seeks while active, and releases output on
   close.
8. Logic export blocks uncertain required media and otherwise produces the
   schema-v2 package described in
   [`RECORDING_AND_LOGIC.md`](RECORDING_AND_LOGIC.md).
9. Support preview and saved archive match and exclude recordings, notes,
   transcripts, Webex content, invitations, secrets, meeting links, and home
   paths.
10. Host sees End Session; guest sees Leave Jam. An active or validating host
    take blocks End Session until **Stop Rec** and **Take saved**. Leave Jam
    finalizes active opted-in guest capture, persists its resumable queue, and
    attempts a final upload before disconnecting. The host waits for any guest
    originals to be verified/arrived before ending because End Session does not
    await peer transfer. Relaunch starts cleanly.
11. The essential flow remains usable at 760×600 with sensible keyboard order,
    visible focus, accessible names, and no color-only status.

The peer recording plane is authenticated HTTP bound to a private RFC1918 IPv4
address. It has no TLS, IPv6, Internet, VPN, NAT-traversal, or public-deployment
claim and no upload quota or rate limiting. Its invite is a reusable
session-scoped bearer credential, not a one-use token. Use trusted bandmates on
a trusted LAN; do not test it by exposing a router port.

## 5. Physical two-Mac and Logic gate

Complete [`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md) with the exact
v0.12.0 ZIP and record:

- both Mac/interface/driver routes and 48-kHz configuration;
- musician-confirmed two-way audibility, not just meters;
- host and opted-in guest originals through an actual Wi-Fi interruption when
  the v2 peer plane is active;
- stable identity, resumed verified delivery, and truthful timeline gaps;
- Studio playback/seek/mixer/output/reopen evidence;
- exact exported inventory/checksums and actual Logic Pro import at `0:00`;
- private support bundle and zero-owned-process cleanup.

At the time this procedure was updated, two-Mac audibility and Logic import are
**NOT RUN**. Keep that state until the worksheet contains real observations.

## 6. Pass rule

The private candidate may reach the default branch after its exact source,
normal CI, exact-bundle, and installed cleanup gates pass; that integration is
not a release, tag, or physical certification. It is ready to advance beyond
the private test only after both musicians and Logic Pro pass the worksheet.
Preserve failed takes, originals, exports, reports, and the support bundle
before changing a device, network, or build. GitHub Actions run `29269188463`
remains the 3,600-second Jamulus/JACK longevity evidence for the v1/v2 engine
baseline; it does not certify v0.12 remote v3, CoreAudio, two Macs, or Logic.

The retired 2024 harness remains in
[`legacy/TEST_PROCEDURE_2024.md`](legacy/TEST_PROCEDURE_2024.md) for history.
