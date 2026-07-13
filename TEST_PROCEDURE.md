# WebJam v0.10.0 certification procedure

**Last updated:** 2026-07-13
**Current target:** private Apple Silicon two-Mac v0.10.0 candidate

The product path is:

> Band Check → Host → Invite → Join → Play → Record → Studio → Export → End

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
The final run must include:

- strict v1/v2 invitation parsing and credential redaction;
- Band Check input/output/scratch/host/Studio/support outcomes;
- stable participant identity and authenticated presence generations;
- local capture absolute-frame gaps, writer timeout, recovery, and shutdown;
- resumable/idempotent size/SHA/PCM-verified guest transfer;
- schema-v2 missing/partial/damaged/segment/rate/project truth;
- Studio seek/waveform/mixer/output/reopen behavior;
- offset/drift/rate/gap alignment and non-destructive manual restoration;
- atomic Logic exports, independent analysis, source checks, and checksums;
- support preview/copy/save parity and adversarial redaction;
- owned-process/port cleanup and fresh-host restart;
- the three-part black/white/burnt-orange brand assets.

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

Do not overwrite the preserved v0.9.0 ZIP. Record these values only after the
v0.10.0 ZIP is final:

```text
Source commit:          [TO FILL]
Artifact filename:      [TO FILL]
Artifact absolute path: [TO FILL]
SHA-256:                [TO FILL]
Fresh extraction path:  [TO FILL]
```

Inspect that exact fresh extraction:

- `Info.plist` reports `0.10.0` and registers the `webjam` URL scheme.
- The bundle contains Band Check, private session transfer, schema-v2 project,
  Studio, export, support preview, brand assets, licenses, Inter, and official
  Jamulus/JamulusServer 3.12.2 resources.
- The app and nested bundles have the intended architecture/version.
- `codesign --verify --deep --strict` succeeds for the complete app.
- The frozen executable starts only the intended background music processes.
- Host opens expected service ports, Record finalizes, End/quit releases them,
  and relaunch can host again with no stale process or port.

This private candidate may be ad-hoc signed and not notarized. Control-click →
Open or Privacy & Security → Open Anyway is an acceptable first-launch step. A
missing/invalid sealed resource or “damaged app” result fails packaging.

## 4. Frozen-flow smoke

Use an isolated preferences/home profile and the exact extracted v0.10.0 app.

1. Launch shows the original three-part WebJam mark and the restrained black,
   white, neutral, and burnt-orange system. No purple or teal remains.
2. Band Check performs explicit input/output/scratch actions, separates its
   local PortAudio evidence from Jamulus send/receive observations, and reports
   one typed outcome. Blank Webex remains optional.
3. Host reaches ready state before exposing Copy Invite. The v2 link is treated
   as a private enrollment credential and never appears in support output.
4. A valid link joins on cold start and while WebJam is already running.
   Malformed/ambiguous links fail safely; a legacy v1 link joins without
   claiming the private recording plane.
5. Host/guest participant identity remains stable across name/channel/reconnect
   changes. No duplicate musician appears.
6. With local originals explicitly enabled, host and guest capture start only
   after confirmed recording state. Peer outage does not stop an active local
   writer, and verified transfer resumes without deleting the guest original.
7. Studio shows missing/partial/damaged/transferring truth, plays mixed-rate
   multi-segment projects with gaps, seeks while active, and releases output on
   close.
8. Logic export blocks uncertain required media and otherwise produces the
   schema-v2 package described in
   [`RECORDING_AND_LOGIC.md`](RECORDING_AND_LOGIC.md).
9. Support preview and saved archive match and exclude audio, personal content,
   invitations, secrets, meeting links, and home paths.
10. Host sees End Session; guest sees Leave Jam. Recording/transfer finalization
    happens before owned service cleanup. Relaunch starts cleanly.
11. The essential flow remains usable at 760×600 with sensible keyboard order,
    visible focus, accessible names, and no color-only status.

The peer recording plane is authenticated HTTP bound to a private RFC1918 IPv4
address. It has no TLS, IPv6, Internet, VPN, NAT-traversal, or public-deployment
claim. Do not test it by exposing a router port.

## 5. Physical two-Mac and Logic gate

Complete [`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md) with the exact
v0.10.0 ZIP and record:

- both Mac/interface/driver routes and 48-kHz configuration;
- musician-confirmed two-way audibility, not just meters;
- host and opted-in guest originals through an actual Wi-Fi interruption;
- stable identity, resumed verified delivery, and truthful timeline gaps;
- Studio playback/seek/mixer/output/reopen evidence;
- exact exported inventory/checksums and actual Logic Pro import at `0:00`;
- private support bundle and zero-owned-process cleanup.

At the time this procedure was updated, two-Mac audibility and Logic import are
**NOT RUN**. Keep that state until the worksheet contains real observations.

## 6. Pass rule

The build is ready for the private physical test only after the final source,
real-harness, at-least-60-minute, exact-bundle, and frozen-flow gates pass. It is
ready to advance beyond the test only after both musicians and Logic Pro pass
the physical worksheet. Preserve failed takes, local originals, exports,
reports, and the support bundle before changing a device, network, or build.

The retired 2024 harness remains in
[`legacy/TEST_PROCEDURE_2024.md`](legacy/TEST_PROCEDURE_2024.md) for history.
