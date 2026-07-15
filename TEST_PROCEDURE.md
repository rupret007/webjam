# WebJam v0.15.0 test-night certification procedure

**Last updated:** 2026-07-14
**Current target:** private Apple Silicon v0.15.0 candidate.
**Exact package:** record the filename, SHA-256, source/build commit, and
package-gate result in [`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md)
after the v0.15.0 bundle is built.

The v0.14.0 package is rollback history only. Its source counts, hash, and
package result are not evidence for v0.15.0. Physical two-Mac audio,
CoreAudio-route, recording/recovery, and optional external-editor import are
**NOT RUN** until musicians record actual observations in the worksheet.

The current-source product path is:

> Host or Join → Confirm sound → Band Check → Play → Record → Review → Export Tracks → End

The candidate adds a single truthful session-conductor action, durable
local-capture checkpoints, truthful recovery projects, and conservative Track
Export selection to the confirmation, CoreAudio-route, storage-preflight, and
private-journal path. Studio is a focused standalone review workspace: one
elapsed-seconds ruler, familiar track headers and transport, compact controls,
and per-take non-destructive mix choices. It deliberately has no invented
bars, beats, tempo, automation grid, plugin host, or editor integration. The
schema-v2 Studio sidecar preserves gain, pan, mute, solo, and export choices by
durable track ID without rewriting the manifest or source audio.

Package checks do not turn any physical Studio, CoreAudio, recovery, or manual
external-editor observation into a pass.

This procedure separates deterministic source evidence, real Jamulus/JACK
evidence, packaged macOS evidence, and physical musician evidence. Passing one
kind never silently fills another.

## 1. Source gate

Run Qt workflows serially; concurrent offscreen processes can share Qt state
and create false failures.

```bash
.venv/bin/ruff check --no-cache \
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
Record the final v0.15.0 source-gate counts, skips, and known warnings here in
the release record; do not carry forward v0.14.0 numbers.
The final run must include:

- strict v1/v2/v3 invitation parsing and credential redaction;
- v3 retry truth: reuse an invitation only when the sidecar failed before
  guest enrollment began; otherwise discard it and require a fresh invitation;
- Band Check input/output/scratch/host/Studio/support outcomes;
- stable participant identity and authenticated presence generations;
- local capture absolute-frame gaps, periodic durable checkpoints, writer
  timeout, recovery-project reconciliation, and shutdown;
- recording-folder write probes, conservative free-storage reserves, visible
  nonblocking warnings, and the guarantee that an unsafe Record attempt arms
  neither recorder;
- resumable/idempotent size/SHA/PCM-verified guest transfer;
- schema-v2 missing/partial/damaged/segment/rate/project truth;
- Studio seek/waveform/mixer/output/reopen behavior, including the v0.15
  shared elapsed-seconds ruler, aligned playhead, selected-track source,
  alignment, and gap inspection, and the compact 760×600 layout;
- schema-v2 Studio sidecar persistence: gain, pan, mute, solo, and Track Export
  inclusion survive reopen by durable `track_id`, while the manifest and WAV
  bytes remain unchanged;
- offset/drift/rate/gap alignment and non-destructive manual restoration;
- atomic Track Exports, independent analysis, source checks, checksums, and
  blocks for selected explicitly silent or unaligned local-original tracks;
  schema-v2 mix/export choices resolve by durable track ID so reordering a take
  cannot move a musician's selection to a neighboring lane;
- support preview/saved-ZIP parity, separate sanitized clipboard-summary
  coverage, and adversarial redaction;
- owned-process/port cleanup and fresh-host restart;
- the three-part black/white/burnt-orange brand assets; and
- session-conductor and hidden Test Night coverage, including one primary action,
  restart truth, bounded local evidence, human-only manual observations, and
  sanitized pilot reports without raw invite, device, path, or audio data.

### Recording durability coverage included in v0.15.0

Run the focused recording checks before packaging a future candidate:

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

This group covers the writable-folder/storage reserve, private 0600
in-progress evidence journal, start/stop/lifecycle checkpoint integration,
periodic writer flush/fsync evidence, private-name/invite/address/credential
redaction, hidden-work-directory discovery exclusion, recovery-project
reconciliation, final-manifest retirement, and Track Export propagation.
Source tests establish this behavior; the physical worksheet remains the only
place to credit actual recording, crash recovery, audibility, and any manual
external-editor import.

### Studio v0.15 review-workspace coverage

Run this focused group before packaging a Studio or conductor change:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q \
  tests/test_recording_studio.py \
  tests/test_studio_state.py \
  tests/test_take_export.py \
  tests/test_session_conductor.py \
  tests/test_host_share_join_flow.py \
  tests/test_test_night_controller.py \
  tests/test_test_night_ui.py \
  tests/test_pilot_evidence.py
```

The tests must establish all of the following without relying on a musical-time
fiction:

- every completed lane uses the same elapsed-seconds extent and ruler; the UI
  shows neither bars/beats nor a tempo claim it cannot substantiate;
- seeking moves the transport, ruler, and displayed lane playheads to the same
  elapsed time, while unavailable media stays truthfully unavailable;
- selecting a lane exposes its recorded source, media/alignment evidence,
  recorded gap truth, and next-export inclusion rather than guessing from a
  waveform;
- the Studio remains operable in the 760×600 compact layout, where contextual
  detail may collapse rather than squeezing track controls out of reach;
- `.webjam-studio-state.json` is a private, atomic, take-bound sidecar that
  restores gain, pan, mute, solo, and export inclusion by durable `track_id`;
  malformed or mismatched state is rejected and neither `webjam-take.json` nor
  source WAV bytes are rewritten; and
- schema-v2 Track Export uses those durable IDs for mix/export state, so an
  added or reordered lane cannot inherit another lane's choice.

This is deterministic source evidence only. The v0.15.0 package review must be
recorded separately, and musician review, physical interruption/recovery, and
optional manual external-editor import are **NOT RUN** until entered in the
physical worksheet.

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

Do not overwrite an earlier rollback ZIP or copy its identity into a new test.
After an archive is built, fresh-extract it and record its exact build commit,
filename, SHA-256, architecture, install location, and package-gate result in
the release record and [`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md).
Until those v0.15.0 facts are recorded, the package and physical worksheets
remain **NOT RUN**.

Verify the bundled official Jamulus 3.12.2 DMG checksum before staging the
nested client and server apps. Only the exact fresh-extracted archive named in
that completed release record may receive package or musician credit.

Inspect that exact fresh extraction:

- `Info.plist` reports version `0.15.0` and registers the `webjam`
  URL scheme.
- The bundle contains Band Check, private session transfer, schema-v2 project,
  Studio, export, support preview, brand assets, licenses, Inter, the
  confirmation/route/storage/journal path, and official Jamulus/JamulusServer
  3.12.2 resources.
- The app and nested bundles have the intended architecture/version.
- `codesign --verify --deep --strict` succeeds for the complete app.
- The signed arm64 `webjam-fabric` matches its canonical manifest under
  `Contents/Resources`, embeds the exact source commit, and passes bounded
  ready/hello/shutdown IPC through the production validator.
- The package gate must pass strict/deep signature verification, nested-app
  inspection, sidecar build/hash/IPC validation, and two isolated six-second
  offscreen launch/TERM cycles before this archive receives package credit.
- Host service ports, live recording, End/quit cleanup with hardware, and a
  fresh host after a real musician run remain physical worksheet checks.

This private candidate may be ad-hoc signed and not notarized. Control-click →
Open or Privacy & Security → Open Anyway is an acceptable first-launch step. A
missing/invalid sealed resource or “damaged app” result fails packaging.

## 4. Candidate-flow smoke

Use an isolated preferences/home profile and the exact extracted v0.15.0 app.

An isolated launch/TERM smoke verifies startup and bounded cleanup only; it is
not a live-audio, route, roster, recording, reconnect, crash-recovery, or
external-editor result. Attach the test interfaces before completing the
musician steps below.

1. Launch shows the original three-part WebJam mark and the restrained black,
   white, neutral, and burnt-orange system. No purple or teal remains. After
   Host or Join, the candidate shows the short name-and-sound confirmation;
   **Band input** and **Band output & review** expose the intended CoreAudio
   route without claiming it is audible.
2. Band Check performs explicit input/output/scratch actions, separates its
   local PortAudio evidence from Jamulus send/receive observations, and reports
   one typed outcome. Blank Webex remains optional.
3. Host reaches ready state before exposing Copy Invite. A same-LAN v2 link is
   treated as a private enrollment credential and never appears in support
   output. A peer-start failure visibly falls back to v1 with guest
   local-original capture and delivery off while preserving join/play and
   host-side server recording. The separate lab-only v3 path is never a public
   remote-hosting claim: it offers **Try Again** only when the sidecar failed
   before guest enrollment began; once enrollment may have begun it discards the
   link and requires a fresh invitation, never a legacy fallback.
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
   recording state. The local writer periodically flushes and fsyncs durable
   checkpoint evidence. A peer outage does not stop an active local writer, and
   v2 verified transfer resumes without deleting the guest original. After an
   abnormal stop, recovered local media must surface as a **NEEDS ATTENTION**
   project for manual review; it is never silently marked complete or claimed
   transferred.
7. Studio shows missing/partial/damaged/transferring truth, plays mixed-rate
   multi-segment projects with gaps, and releases output on close. Its v0.15
   review workspace uses one elapsed-seconds ruler—not invented bars or
   tempo—keeps its transport/ruler/lane playheads aligned while seeking, and
   lets a selected lane expose source, alignment, and gap evidence. At compact
   size, contextual detail may collapse but essential track controls remain
   reachable.
8. **Export Tracks** blocks uncertain required media, a selected explicitly silent
   performance track, and a selected local original without verified timeline
   alignment. Studio may non-destructively leave a reviewed track out of the
   future exports until changed; its private sidecar restores those choices by
   durable track ID, not lane position, and it does not alter the take. Keep
   the Jamulus server track or align and verify a local original before treating
   an export as timing-ready. The result is a generic portable Track Export;
   WebJam does not launch or integrate with another editor.
9. Support preview and saved archive match and exclude recordings, notes,
   transcripts, Webex content, invitations, secrets, meeting links, and home
   paths.
10. Host sees End Session; guest sees Leave Jam. An active or validating host
    take blocks End Session until **Stop Rec** and **Take saved**. Leave Jam
    finalizes active opted-in guest capture, persists its resumable queue, and
    attempts a final upload before disconnecting. The host waits for any guest
    originals to be verified/arrived before ending because End Session does not
    await peer transfer. Relaunch starts cleanly.
11. In operator-only `--test-night` mode, the Test Night action can start,
    pause, resume, abandon, restart, record explicitly human observations, and
    export a redacted report. It is not shown in normal musician mode.
12. The essential flow remains usable at 760×600 with sensible keyboard order,
    visible focus, accessible names, and no color-only status.

The peer recording plane is authenticated HTTP bound to a private RFC1918 IPv4
address. It has no TLS, IPv6, Internet, VPN, NAT-traversal, or public-deployment
claim and no upload quota or rate limiting. Its invite is a reusable
session-scoped bearer credential, not a one-use token. Use trusted bandmates on
a trusted LAN; do not test it by exposing a router port.

## 5. Physical two-Mac and optional external-editor gate

Complete [`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md) with the exact
recorded v0.15.0 archive and record:

- both Mac/interface/driver routes and 48-kHz configuration;
- musician-confirmed two-way audibility, not just meters;
- host and opted-in guest originals through an actual Wi-Fi interruption when
  the v2 peer plane is active, plus any recovery project as **NEEDS ATTENTION**
  until a musician manually reviews it;
- stable identity, resumed verified delivery, and truthful timeline gaps;
- Studio playback/seek/mixer/output/reopen evidence, including the
  elapsed-seconds shared ruler (with no fabricated bars/beats), selected-track
  source/alignment/gap inspection, and a usable compact layout;
- a before/after check that the Studio sidecar restores the same durable-ID mix
  and export choices after reopen without changing `webjam-take.json` or any
  source WAV;
- a Track Export in which an intentionally excluded source stays excluded by
  its durable identity rather than following a display position;
- exact exported inventory/checksums and, only if performed, manual import into
  an external multitrack editor at `0:00`;
- private support bundle and zero-owned-process cleanup.

At the time this procedure was updated, two-Mac audibility, physical CoreAudio
route confirmation, recording and interruption recovery, Studio use, and
manual external-editor import are **NOT RUN**. Keep that state until the
worksheet contains real observations.

## 6. Pass rule

The private candidate may reach the default branch after its exact source,
normal CI, exact-bundle, and installed cleanup gates pass; that integration is
not a release, tag, or physical certification. It is ready to advance beyond
the private test only after both musicians complete the worksheet. Manual
external-editor import is optional and is never an integration requirement.
Preserve failed takes, originals, exports, reports, and the support bundle
before changing a device, network, or build. GitHub Actions run `29269188463`
remains the 3,600-second Jamulus/JACK longevity evidence for the v1/v2 engine
baseline; it does not certify the lab-only v3 profile, CoreAudio, two Macs, or
manual external-editor import.

The retired 2024 harness remains in
[`legacy/TEST_PROCEDURE_2024.md`](legacy/TEST_PROCEDURE_2024.md) for history.
