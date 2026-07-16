# WebJam real-session certification v1

This is the historical recovery record, architecture truth, and evidence
ledger for the v0.10 certification effort. Its results remain valid historical
evidence. The current v0.16.2 test-build and physical-evidence boundary is
[`README.md`](../../README.md) and [`SUNDAY_TWO_MAC_PILOT.md`](../../SUNDAY_TWO_MAC_PILOT.md).
The preserved
v0.12.0, v0.13.0, and v0.14.0 packages remain evidence only for themselves.
Historical references below to a Logic export name do not describe the current
product: v0.16 has a Logic-like Studio review surface and does not integrate
with an external editor.

## Product promise

> Open WebJam. Run Band Check. Invite the band. Play and record with confidence.

## Evidence language

- **Implemented** means the code exists in the shared working tree.
- **Deterministic pass** means an automated test exercised that code without
  physical audio hardware.
- **Real-harness pass** means official Jamulus 3.12.2 processes exchanged or
  recorded measured PCM through JACK hardware-boundary ports. It is not a
  physical macOS or acoustic result.
- **Physical pass** means the named Macs, interfaces, headphones, musicians,
  and application were actually used and the worksheet was completed.
- **NOT RUN** is a required result, not a euphemism for “probably works.”

## Guardrails

- Never claim a physical two-Mac, acoustic audibility, or Logic Pro result
  without performing it on the named hardware/application.
- Do not tag, notarize, publish, or overwrite the preserved v0.9.0 artifact.
- Keep Jamulus as the low-latency music path and Webex optional for video and
  conversation.
- Preserve original recordings and unrelated user work.
- Test-only injection must enter at the narrowest hardware boundary and then
  use the production recording/project/Studio/export path.
- An authenticated transport is not automatically an encrypted transport.
  WebJam's peer recording service is same-LAN HTTP over RFC1918 IPv4 only; it
  does not provide TLS, Internet exposure, VPN traversal, or IPv6 support.

## Historical v0.13 package addendum — physical evidence still separate

This section records the v0.13 implementation/package boundary. It is not the
current candidate status: use the v0.16.2 release record and pilot worksheet
for that.

- **Interrupted local capture:** the writer periodically flushes and
  synchronizes its audio before recording opaque IDs, durable frames, gaps, and
  capture facts. Recovery promotes only safe abandoned capture directories to
  visible **Needs Attention** projects. Frames past the final durable checkpoint
  are declared unverified/partial; they are not called a completed take.
- **Guest recovery:** a crash-recovered guest original is preserved locally for
  review. It is not automatically queued for transfer or represented as having
  reached the host.
- **Logic export:** Studio keeps track-selection state outside the immutable
  manifest and fails closed for a selected explicitly silent track or an
  unaligned guest/local original. The musician must deselect it, use the
  aligned server track, or align and verify it first.
- **Remote v3 invitation:** the separate loopback/CI laboratory profile may
  retry only before `open_guest` begins enrollment. Once enrollment has been
  attempted, the invitation is cleared and a fresh one is required. This does
  not change the reusable v2 same-LAN invitation described in the historical
  architecture below, nor does it establish public remote service.

The v0.13.0 artifact remains historical package evidence. The current v0.16.2
test build's fresh-extraction package verification and ad-hoc signing result
are recorded in the README and GitHub release notes. Physical interruption
recovery, two-Mac results, and external-editor import remain **NOT RUN**.

## Repository and preserved baseline — 2026-07-13

- Repository: `/Users/jeffstory/Claude/Projects/WebJam/repo`
- Starting branch: `master`
- Starting/local/remote commit:
  `d2207e5d8a5c79d9e4c294bb90f4cb76cc234d55`
- Starting tree was clean; `HEAD == origin/master`; divergence `0 0`.
- Working branch: `codex/webjam-real-session-certification-v1`.
- Latest repository tag at recovery: `v0.8.0`; no new tag was created.
- Preserved v0.9.0 test-night artifact:
  `/Users/jeffstory/Documents/WebJam 2/WebJam-v0.9.0-TEST-NIGHT-macos-arm64.zip`
- Observed preserved artifact SHA-256:
  `d7f3ae5e76f0673d255b759a1577d166b8002bad828b5df1292c86e6f79dfb62`
- That SHA was traced to GitHub Actions macOS ARM64 run `29240738788`.
  The objective-reported local pre-CI artifact is separately preserved at
  `/Users/jeffstory/Documents/WebJam 2/Superseded/WebJam-v0.9.0-LOCAL-PRE-CI-d2207e5.zip`.
- A fresh v0.9.0 extraction passed strict outer/nested signature checks;
  WebJam reported `0.9.0` arm64 and bundled Jamulus/JamulusServer reported
  `3.12.2`.
- The preserved v0.9.0 artifact is baseline evidence only. It does not contain
  the current certification changes and must not be handed out as the new
  candidate.

## Non-negotiable physical evidence boundary

- Two independent Macs: **NOT RUN**.
- Two-musician bidirectional acoustic audibility: **NOT RUN**.
- Actual macOS interface selection matching the Jamulus route: **NOT RUN**.
- Guest isolated original captured during a real outage and delivered to the
  host: **NOT RUN**.
- Logic Pro import and playback: **NOT RUN**.
- Local Docker ARM at-least-60-minute soak: **FAILED after 667.201 seconds**;
  the 73rd measured cycle had 35/105 silent 20-ms windows. Cleanup was clean.
- Native Ubuntu at-least-60-minute workflow: initial run `29261331713` was
  deliberately **CANCELLED as insufficient** after packaged smoke exposed a
  release-blocking Host UI freeze in source `6ebc883`. Replacement GitHub
  Actions run `29262915880` was also deliberately **CANCELLED as
  insufficient** after about ten minutes because later recovery fixes changed
  the candidate. Exact-source run `29269188463` passed against
  `8ee89081802fe5998f71299c4755b21ae5218cb9`: 3,602.851 seconds of measured
  transport, 396 cycles, three recording/restart cycles, forced reconnect,
  bounded resources/xruns, 12 WAV stems, and zero cleanup errors.

The five physical items above remain gates; the native pass does not fill them.

## Vertical-slice checklist

### 1. Baseline recovery and packaged inspection

- [x] Read the complete certification objective.
- [x] Verify source and `origin/master` at `d2207e5`.
- [x] Create the dedicated working branch and this recovery record.
- [x] Resolve the old candidate SHA mismatch without overwriting either file.
- [x] Exercise the preserved v0.9.0 packaged Host, bundled Jamulus client and
  server, Record, manifest creation, finalization, and normal cleanup.
- [x] Build a new candidate from the integrated tree.
- [ ] Exercise new-candidate cold Host and Join/deep-link, Band Check, Record,
  reconnect, Studio, support bundle, Logic export, End/Leave, and relaunch.
- [x] Record the new candidate's final version, commit, path, SHA-256,
  signatures, and extraction directory.

### 2. Production audio and recording architecture

- [x] Map WebJam's selected PortAudio input to its meter/local isolated writer.
- [x] Map the separate Jamulus client/server and server-recorder path.
- [x] Record formats, rates, channels, queue behavior, finalization, recovery,
  device metadata, and explicit gap behavior in source and tests.
- [x] Preserve queue/write-loss time by writing silence at exact source-frame
  positions and recording gap intervals instead of compressing the take.
- [x] Add stable take, participant, track, source, and segment UUIDs.
- [ ] Physically prove the selected PortAudio capture is the intended interface
  and does not contain Webex/system/loopback audio.
- [ ] Physically prove Jamulus is using the intended input/output interface.

### 3. Two-client deterministic certification harness

- [x] Start one real Jamulus 3.12.2 server and two independently named real
  clients on a prepared Linux/JACK boundary.
- [x] Inject distinct deterministic click/tone/silence fixtures only through
  each client's JACK capture ports and capture its JACK playback ports.
- [x] Assert sample rate, channels, frames, dominant frequency, RMS, peak,
  silence, dropout windows, cross-contamination, roster identity, and cleanup.
- [x] Exercise real Jamulus server stems through project loading, the same
  `TakePlayer` core used by Studio, and the Logic export implementation.
- [x] Add an opt-in short recorder/reconnect/resource rehearsal.
- [x] Add a separate opt-in test that refuses to count as longevity evidence
  unless the requested signal duration is at least 3,600 seconds.
- [x] Record the failed local Docker ARM soak without treating its clean first
  72 cycles or clean shutdown as a longevity pass.
- [x] Record exact-source native Ubuntu at-least-60-minute workflow
  `29269188463` against `8ee8908`; preserve `29261331713` and `29262915880`
  as **CANCELLED/INSUFFICIENT** and do not count them.
- [ ] Repeat the critical route/acoustic proof on two physical Macs.

### 4. Permanent Band Check

- [x] One guided flow from first run, F2, audio settings, and active
  troubleshooting when the stored verification signature is stale.
- [x] Typed outcomes: Ready to Jam / Ready with a Warning / Action Needed.
- [x] Input meter distinguishes silence/capture/clipping but says only that
  WebJam's local PortAudio input is audible to WebJam.
- [x] Conservative output/left/right action and headphone guidance.
- [x] Five-second isolated PCM24 recording, exact scan, quiet playback, and
  explicit “That sounds right” confirmation.
- [x] Production host-server lifecycle check with authenticated recorder state,
  owned-process stop, and port-release verification.
- [x] Production `TakePlayer` exercise for play/stop/seek/mute/solo/gain/pan
  with source-hash immutability.
- [x] Live-observe mode never starts a second device, service, playback, or
  recording stream.
- [x] Technical Details hidden by default and non-color status semantics.
- [ ] Perform the musician-confirmed input, output, and audibility actions on
  each physical Mac.

### 5. Recording, reconnect, identity, and transfer resilience

- [x] Local capture is independent of peer HTTP availability and continues
  through a control-plane outage in deterministic integration tests.
- [x] Gaps and reconnect media intervals are explicit schema-v2 segments.
- [x] A private installation UUID derives a stable session participant UUID;
  authenticated generation-bound presence survives channel/name changes.
- [x] The host and active-v2 opted-in guests can retain local isolated
  originals; v1 guests cannot use WebJam-orchestrated local capture.
- [x] Uploads are resumable, idempotent, size/SHA/PCM verified, and published
  atomically without moving or deleting the guest's original.
- [x] Partial files remain visible; missing, receiving, partial, damaged, and
  transfer-failed media remain truthful in the take inventory.
- [x] Abandoned hidden local captures are promoted to visible recovery folders
  with metadata; a live writer and symlink target are not adopted.
- [x] Device/rate changes preserve the started configuration and mark the take
  for attention rather than relabeling its audio.
- [x] Duplicate state/chunk/finalize calls are covered by deterministic tests.
- [ ] Prove outage capture, reconnect identity, resumed delivery, and duplicate
  action safety with two physical Macs.

### 6. Studio, alignment, and Logic handoff

- [x] Schema-v2 loading retains missing/damaged/partial/transferring media,
  nested segment paths, rates, reconnect placement, and gap truth.
- [x] Player handles multi-segment, mixed-rate, drift-adjusted project time,
  gaps, active seek, mute, multi-solo, gain, pan, and output release.
- [x] Studio builds exact composite waveforms asynchronously and discloses
  unavailable or uncertain media instead of silently dropping it.
- [x] Alignment uses bounded transient scans, signed offset, multiple anchors,
  long-take drift, native rates, explicit segments, residuals, and immutable
  automatic/manual transforms.
- [x] Schema-v2 Logic export renders selected common-origin PCM24 stems,
  optional processed stems, `WebJam Server Reference.wav`, `WebJam Studio
  Reference.wav`, a marker CSV, tempo/time-signature metadata, source manifest,
  alignment and recording reports, independent WAV analysis, and checksums.
- [x] Changed, missing, damaged, or incomplete media blocks atomic export.
- [x] Export evidence explicitly says Logic Pro was not physically verified.
- [ ] Import the package into Logic Pro at 0:00 and verify tracks, duration,
  alignment, markers, references, and audible identity.

Export limitation: mixed-rate/drift conversion is deterministic linear affine
interpolation (`deterministic-linear-affine-v1`). It is disclosed and tested,
but it is not claimed to be a sample-perfect or mastering-grade resampler.
`WebJam Server Reference.wav` is an offline unity mix of post-network Jamulus
server tracks, not an independently captured acoustic/live-output feed.

### 7. Webex guidance and privacy-safe support bundle

- [x] Explain that Jamulus carries music while Webex is optional for
  video/conversation.
- [x] Blank Webex is optional and never blocks Band Check.
- [x] Warn about delayed duplicate monitoring without changing enterprise
  Webex settings.
- [x] Preview and saved ZIP derive from one immutable allowlisted support
  artifact; the shortcut separately creates a sanitized clipboard summary.
- [x] The default bundle excludes settings dumps, environment dumps, audio,
  notes, transcripts, meeting links, invitation tokens, secrets, arbitrary
  files, and personal paths.
- [x] Bounded text-log tails are recursively redacted; archive publication is
  atomic and mode 0600.
- [ ] Verify the final integrated UI button saves the same previewed artifact
  in a fresh packaged candidate.

### 8. Failure and long-session validation

- [x] Deterministic coverage exists for queue overflow, write failure, writer
  timeout/recovery, stale captures, interrupted/resumed/conflicting transfers,
  checksum/PCM mismatch, missing media, reconnect gaps, mixed rates, seek,
  and server ownership cleanup.
- [x] Short real-JACK rehearsal exercised repeated transport, a recorder
  restart, client disconnect/reconnect, resource sampling, finalized WAVs, and
  zero-owned-process cleanup.
- [x] Preserve and record the failed 667.201-second local Docker ARM soak.
- [x] Record exact-source native Ubuntu workflow `29269188463`; its
  machine-readable report passed every one-hour signal, recorder, reconnect,
  resource, xrun, and cleanup assertion.
- [ ] Finish the remaining packaged/physical matrix: device removal and
  reappearance, microphone permission, full/low disk, invalid directory,
  sleep/wake, firewall, output failure, host restart, and UI close races.
- [ ] For every physical failure, record truthful UI, next action, preserved
  media, idempotent retry, technical details, and eventual cleanup.

### 9. Release-candidate evidence

- [x] Run the final integrated full suite, production Ruff, compile,
  dependency/vulnerability, privacy, UX, and local build gates.
- [x] Complete the exact candidate's packaged runtime smoke after its passed
  build, nested signing, ZIP, hash, and fresh-extraction gates.
- [x] Maintain a concise physical worksheet at
  [`SUNDAY_TWO_MAC_PILOT.md`](../../SUNDAY_TWO_MAC_PILOT.md).
- [ ] Complete the worksheet on two Macs and attach its evidence.
- [x] Keep all unavailable physical evidence explicitly **NOT RUN** and do not
  call the candidate physically certified or release-ready before it exists.

### 10. Product identity and visual restraint

- [x] Replace the placeholder `WJ` with one original three-part WebJam mark for
  conversation, live music, and production.
- [x] Keep the mark legible at small sizes, one-color capable, and distinct
  from Webex, Jamulus, Logic, and third-party trademarks.
- [x] Generate SVG, ICO, and ICNS assets and use the mark in the app/header.
- [x] Use black, white, neutral gray, and burnt orange only; no purple or teal.
- [x] Add brand-asset, app-data, render, compile, and macOS bundle checks.

## Acceptance matrix

| Area | Automated evidence | Physical/final state |
| --- | --- | --- |
| Band Check | Implemented; focused suites and lifecycle/adversarial cases pass | Manual input/output/acoustic confirmation **NOT RUN** |
| Two-client audio | Real Jamulus 3.12.2 + two JACK clients exchange distinct measured signals; short rehearsal passes | Two-Mac audibility **NOT RUN** |
| Isolated recording | Host/active-v2-guest opt-in local capture, explicit gaps/recovery, resumable verified delivery implemented and deterministically tested; v1 guest capture is unavailable | Real two-interface originals/outage delivery **NOT RUN** |
| Reconnect | Stable session identity, generation binding, continued capture, resumable upload, truthful segment inventory tested | Physical Wi-Fi interruption **NOT RUN** |
| Studio | Schema-v2 multi-segment/mixed-rate/gap playback, seek, waveform, mixer, and media truth pass focused tests | Packaged and long-take physical review pending |
| Alignment | Offset/drift/rate/gap fixtures and non-destructive transforms pass focused tests | Real musician material pending |
| Logic handoff | Complete schema-v2 package, reports, analyses, and checksum behavior pass focused tests | Logic Pro import **NOT RUN** |
| Diagnostics | Immutable allowlist preview/saved-ZIP parity, separate sanitized clipboard summary, and adversarial redaction pass | Fresh packaged-button check pending |
| Long session | Exact-source native run `29269188463` passed 3,602.851 measured seconds, 396 cycles, three recording/restart cycles, reconnect, signal/resource/xrun gates, and zero cleanup errors | Local Docker ARM failure and cancelled runs remain preserved but do not count; physical long-session review remains separate |
| Physical two-Mac | Worksheet is ready | **NOT RUN** |
| Historical v0.10 candidate | v0.10.0 build, nested signing, ZIP, hash, fresh extraction, two bundled client/server authenticated lifecycles, normal Qt close, and cleanup pass at source `8ee8908` | Superseded by the v0.12 private test-night handoff; its historical physical matrix remains evidence only |

## Defect log

| ID | Severity | State | Evidence / remaining boundary |
| --- | --- | --- | --- |
| CERT-001 | High | Resolved | Old artifact provenance and SHA mismatch resolved without overwriting either file. |
| AUD-001 | Critical | Open physical boundary | WebJam's meter/local writer opens PortAudio separately from Jamulus. It cannot prove Jamulus selected the same device or that a virtual interface excludes Webex/system audio. Band Check copy now preserves this distinction. |
| AUD-002 | Critical | Resolved deterministically | Local queue/write loss keeps absolute frame time, inserts silence, and records gaps. |
| AUD-003 | Critical | Resolved in source | Active-v2 opted-in guests retain local originals and upload verified copies; v1 guests have no WebJam capture path. Physical guest capture/transfer remains **NOT RUN**. |
| AUD-004 | Critical | Resolved deterministically | Durable installation/session/participant/take/track/segment identities replace mutable name/channel identity. |
| AUD-005 | High | Resolved for harness | Real two-client Jamulus/JACK boundary harness measures exchange, recorder stems, Studio core, export, and cleanup. It does not replace the macOS gate. |
| AUD-006 | High | Resolved deterministically | Writer timeout retains ownership and schedules visible recovery; abandoned captures are safely adopted on startup. |
| AUD-007 | Critical | Resolved deterministically | Active Studio seek reopens/positions every segment reader. |
| AUD-008 | Critical | Resolved deterministically | Declared missing/damaged/partial/transfer media remains in project and blocks false complete/export states. |
| AUD-009 | High | Resolved deterministically | Exact bucketed composite waveform work is asynchronous/cache-compatible and includes segment gaps/rates. |
| AUD-010 | High | Resolved deterministically | Leaving/shutting Studio stops playback and releases its output sink. |
| AUD-011 | High | Resolved | Band Check has typed outcomes and separates local PortAudio evidence from Jamulus observations. |
| AUD-012 | High | Resolved | Blank Webex remains optional; input device index `0` is preserved. |
| AUD-013 | Critical | Resolved deterministically | Support export is allowlist-first, private, recursively redacted, and parity-tested across preview/saved ZIP; the separate clipboard summary is sanitized. |
| AUD-014 | High | Resolved deterministically | Alignment covers multiple anchors, signed offset, drift, rates, gaps, residuals, and manual restoration. |
| AUD-015 | High | Resolved in source | Schema-v2 Logic package contains aligned selectable stems, references, musical metadata, reports, analysis, source manifest, and checksums. Physical Logic is **NOT RUN**. |
| AUD-016 | High | Resolved by exact-source native evidence | The local Docker ARM failure remains preserved. Runs `29261331713` and `29262915880` were cancelled and do not count. Run `29269188463` passed 3,602.851 measured seconds with zero decoded dropout windows, bounded resources/xruns, forced reconnect, recorder cycles, and clean shutdown. |
| AUD-017 | Critical scope limit | Accepted for trusted private pilot only | Peer recording control/transfer is authenticated same-LAN HTTP bound to RFC1918 IPv4. There is no TLS, IPv6, Internet, VPN, NAT traversal, upload quota, or rate limiting. Treat invites as private credentials and do not use this plane with untrusted users/hostile LANs. |
| AUD-018 | High | Open physical gate | The integrated full suite, fresh-package integrity, two packaged Host/client cleanup runs, and native longevity gate pass. Two-Mac certification and Logic Pro import remain open. |
| AUD-019 | Critical | Resolved and packaged | Packaged Host could freeze its UI in reverse DNS while binding the private peer service. `f4d04c7` bypasses `HTTPServer` name lookup, has a regression that fails on `getfqdn`, and completes the full frozen Host lifecycle normally. |

## Architecture truth

### Live music path

**Historical boundary (the certification evidence recorded below):** at the
time this plan was written, `BridgeService` started Jamulus with server, client
name, and RPC arguments only. It did not choose or expose Jamulus's
input/output device, channel map, sample rate, buffer, or PCM. Current macOS
source now resolves persistent CoreAudio input/output IDs, stages a WebJam-owned
Jamulus route file, and freezes that selection across reconnects; this plan's
older evidence does not certify that newer route behavior. `AudioEngine` still
opens a separate PortAudio/sounddevice input for WebJam's meter. Jamulus RPC
exposes scalar levels, not PCM. Therefore a moving WebJam meter proves only
that WebJam's PortAudio stream sees signal; it does not prove what Jamulus sends
or what a bandmate hears.

The deterministic harness uses one real Jamulus 3.12.2 server and two real
clients with a JACK dummy device. Fixtures enter through each client's public
JACK capture ports before Jamulus encoding, and received PCM leaves through its
JACK playback ports after decoding. This is a real codec/transport boundary,
but it is Linux/JACK and not the bundled macOS/Core Audio route.

### Local isolated recording path

When explicitly enabled, `LocalInputCapture` opens the selected two-channel
PortAudio device at 48 kHz and writes separate PCM24 mono originals through a
bounded queue. The callback advances an absolute frame counter before enqueue;
the writer pads every dropped or failed interval with silence and records exact
gap metadata. Device identity, backend, rate, channels, start time, errors, and
gaps enter the schema-v2 project. Capture never deliberately mixes remote
Jamulus or Webex audio, but a virtual/loopback hardware selection could still
contain it; the physical worksheet must prove the actual route.

The writer owns its handles until released. Timeout/attach failures retain
visible recovered partial WAVs and recovery metadata. Startup recovery moves
abandoned hidden capture directories into visible recovery folders, skips live
PIDs, and does not follow symlinks.

Current v0.13 source further distinguishes frames safely synchronized before an
interruption from later buffered frames. Recovery records that durable boundary
and marks any later media unverified/partial rather than silently treating the
whole WAV as final. This is source behavior, not physical recovery evidence.

### Server/reference recording path

JamulusServer writes one post-network track per visible channel plus its native
metadata. WebJam validates and hashes these files, then writes schema-v2 project
truth with stable identities and explicit segments. Server tracks are
`jamulus_server` / `network_track`: they are useful post-network evidence, not
local isolated originals. The exported `WebJam Server Reference.wav` is a
deterministic offline unity mix of those server tracks; the label does not mean
an independent acoustic/live-output recording.

### Identity, reconnect, and transfer

A private installation UUID deterministically maps to a session participant
UUID. The host binds that durable participant to the current Jamulus channel
using an authenticated monotonic generation. A v2 invitation includes the
Jamulus endpoint plus session UUID, host peer port, and a random enrollment
credential. That invitation is a reusable session-scoped bearer rather than a
one-use or one-guest token: every installation presenting it on the LAN can
enroll until the host peer restarts. Legacy v1 links still join Jamulus and
receive a host-side server track, but do not enable WebJam-orchestrated guest
local capture or the private recording/transfer plane.

The reusable v2 LAN invitation above is not the same as the v3 remote-lab
invitation. Current v0.13 source retains a v3 invitation only when its native
sidecar fails before enrollment starts; after `open_guest` is attempted it
requires a fresh invitation rather than retrying a possibly consumed credential.

The host peer service binds only to a private RFC1918 IPv4 address and an
ephemeral port. It uses authenticated plain HTTP, not HTTPS/TLS. It does not
support IPv6, routable Internet addresses, VPN/NAT traversal, or public
deployment. After enrollment, participant tokens authenticate presence,
recording-state observation, and transfer. Session credentials rotate when the
host peer service restarts.

An opted-in guest starts local capture only after observing authenticated host
recording state. A peer outage records an error but does not stop capture.
Finalized immutable segments upload in restartable chunks. The host derives all
paths from validated UUIDs, rejects offset conflicts and mismatched size/SHA/PCM
facts, keeps `.part` state visible, and atomically attaches only verified bytes.
The original stays on the guest Mac.

### Project, Studio, alignment, and export

Schema v2 retains participant/track/segment IDs, source type and quality,
device metadata, hashes, size, rate, channels, format, project start, gaps,
media status, and automatic/manual alignment. Studio and export consume that
same truth; unavailable media is not silently omitted.

`TakePlayer` streams segments on one project clock, including reconnect gaps,
mixed source rates, and drift scale. Alignment estimates signed offset and
drift from repeated transients without editing source audio. Manual nudge is
stored separately and can be restored to automatic evidence.

The schema-v2 Logic package is atomic. It contains numbered common-origin
PCM24 WAVs, optional processed stems, server/studio reference mixes, musical
metadata, source/alignment/recording/analysis evidence, and SHA-256 checksums.
Original source media is hash-checked and never modified. Logic Pro physical
verification remains false until the worksheet is completed.

Current v0.13 source also stops the package before publication if a selected
track is explicitly silent or if a selected guest/local original is unaligned.
The Studio selection can be changed without altering the manifest; a musician
can deselect that track, use the aligned server track, or align and verify it.

### Band Check and support bundle

Band Check uses a GUI-free typed state model. Pre-session tests require
explicit user actions before input metering, tone playback, or scratch capture.
Live-observe mode reads existing process/RPC/recorder/level evidence and never
opens a second device or changes a running service. The locally stored
verification record contains app/Jamulus versions, role, devices,
rate/channels, outcome, manual confirmations, and an informational timestamp.
Startup reuse requires a usable outcome/confirmations and an exact signature
match; the timestamp is recorded for evidence but does not expire the check.

Within the save workflow, support preview, copy-text representation, JSON,
manifest, and ZIP derive from one cached immutable allowlist artifact. The
separate shortcut creates a new sanitized clipboard summary. The archive
excludes recordings, notes, transcripts, Webex content, private invites,
secrets, and home paths by default; it recursively redacts bounded text-log
tails, publishes atomically, and uses mode 0600.

## Command and evidence log

### 2026-07-13 — preserved baseline

```text
git rev-parse HEAD / origin/master
  d2207e5d8a5c79d9e4c294bb90f4cb76cc234d55 / same
git rev-list --left-right --count HEAD...origin/master
  0  0
preserved ZIP SHA-256
  d7f3ae5e76f0673d255b759a1577d166b8002bad828b5df1292c86e6f79dfb62
fresh v0.9.0 extraction codesign --verify --deep --strict
  outer app and bundled Jamulus/JamulusServer passed
packaged isolated-home Host
  WebJam, Jamulus, and JamulusServer started; expected UDP/TCP ports opened
packaged Record -> Stop
  one 48-kHz mono server WAV; manifest status complete
packaged End Session
  owned children exited; expected ports released
```

### 2026-07-13 — Band Check, host lifecycle, and support

```text
Band Check focused regression set
  271 passed, 3 subtests passed
host lifecycle + canonical support/controller regression set
  343 passed, 3 subtests passed
preflight-focused set after device-index/Webex fixes
  30 passed, 3 subtests passed
support/redaction focused set reported by its slice
  86 passed
interim full suite at that point (not the final integrated gate)
  1206 passed, 14 skipped, 1 stale blank-Webex assertion failed
Ruff and bytecode compilation for each reported slice
  passed
physical input/output
  NOT RUN
```

### 2026-07-13 — capture, identity, transfer, Studio, alignment, export

```text
local capture recovery/timeline focused suite
  14 passed
session transfer protocol focused suite
  12 passed
session transfer runtime + adjacent integration reported before later root edits
  13 runtime tests + 74 adjacent integration tests passed
take project/library/player/export/Studio combined suite after regression fix
  86 passed
schema-v2 Logic export focused suite during implementation
  7 passed
alignment and adjacent suite
  69 passed
brand mark/assets/UI/package-focused suite
  109 passed
Ruff / compile / git diff --check for reported slices
  passed
final post-integration full suite
  1350 passed, 17 skipped, 1 warning, 6 subtests passed in 58.71 s
production Ruff (webjam_qt core ui services api)
  passed
compileall, UX smoke, workflow YAML, and git diff --check
  passed
pip check, pip-audit --local, and pip-audit -r requirements.txt
  passed; no broken requirements or known vulnerabilities reported
```

The final integrated suite supersedes the earlier interim failure count. Ruff
was intentionally applied to production code, matching CI; a whole-tests-tree
Ruff scan still reports 11 pre-existing findings in otherwise passing tests.

### 2026-07-13 — real Jamulus/JACK boundary evidence

```text
real server/client version
  Jamulus 3.12.2; one server and two independently named clients
fixture route
  client JACK capture -> Jamulus encode/server/decode -> peer JACK playback
short rehearsal report
  .jamulus-jack-smoke.json
result
  success=true; wall_duration_s=65.677; requested signal duration=18.0 s
transport
  3 measured cycles; client dropout-window maximums 0 and 0
  minimum cross rejection 37.5539 dB and 41.7446 dB
recording
  4 finalized WAVs / 8 total artifacts / 7,198,564 bytes
reconnect
  one client disconnect/reconnect; recovery_s=34.272
resources
  fd growth 0 for jackd/server/both clients
  RSS growth: jackd 0 KiB; server 2,896 KiB; clients 76/100 KiB
cleanup
  no errors; jackd/server/both clients exited with code 0
```

This 65.677-second rehearsal proves the harness machinery and short recovery
path only. It does **not** satisfy the required 3,600-second longevity gate.

### 2026-07-13 — failed local Docker ARM longevity attempt

```text
external report (not committed)
  /Users/jeffstory/Claude/Projects/WebJam/soak-artifacts/
    jamulus-jack-soak-native-arm64.json
result
  FAILED after 667.201 seconds; did not reach the required 3,600 seconds
signal
  cycles 1-72 passed; cycle 73 had 35/105 silent 20-ms windows
JACK
  1,889 xruns; 2.831/s, below the configured 10/s raw-xrun ceiling
reconnect
  not reached before the signal failure
cleanup
  clean; no cleanup errors; jackd/server/both clients exited with code 0
```

The bounded xrun rate and clean cleanup do not override the decoded-signal
failure. This run is preserved as a real defect result, not a partial pass.

### 2026-07-13 — exact-source native one-hour certification

```text
GitHub Actions run
  29269188463; workflow_dispatch; source 8ee89081802fe5998f71299c4755b21ae5218cb9
evidence artifact
  jamulus-jack-soak-29269188463-1
  JSON SHA-256 f2a31871d2b8ca9ca711e3ca3666d5be8b330f1224fbc1cfaebea39520bed82a
  pytest-log SHA-256 7f6ac1ffeed285f10b4efa632456c774ebe92986065058bbc67a337b113d741a
result
  success=true; requested_duration_s=3600.0
  actual_soak_duration_s=3602.851449585; wall_duration_s=3614.719
transport and signal
  396 measured cycles; 122 raw JACK xruns; 0.033862/s against 10.0/s ceiling
  decoded dropout-window maximums 0 and 0
  minimum cross rejection 50.032956 dB and 51.650763 dB
recording
  3 completed cycles; 3 recorder restarts
  12 WAVs / 24 total artifacts / 20,823,596 bytes
reconnect
  forced at 1192.449 s; both stable names returned in 34.526 s
resources
  FD growth 0 for jackd/server/both clients against an 8-FD ceiling
  RSS growth: jackd 0 KiB; server 148 KiB; clients 0/0 KiB
  configured RSS-growth ceiling 65,536 KiB
cleanup
  no errors; jackd/server/both clients exited with code 0
  allowed live processes, occupied ports, and private JACK servers: 0
```

The report and pytest log are the always-uploaded evidence from the successful
workflow, not a local reconstruction. The full run also passed source tests,
real-Jamulus integration, and macOS ARM64, macOS x64, and Windows x64 builds.
Runs `29261331713` and `29262915880` remain **CANCELLED/INSUFFICIENT** and are
not counted as longevity evidence.

### 2026-07-13 — packaged reverse-DNS defect and fixed candidate

The first v0.10.0 package from `6ebc883` was rejected after an eight-second
frozen Host smoke stopped making UI progress. A process sample proved all
763/763 main-thread samples were blocked in reverse DNS from
`HTTPServer.server_bind()` while the private peer service bound its RFC1918
address. It was not a microphone-permission wait.

Commit `f4d04c7d6151295e4098428f2d1a9e2d7e5a0853` now binds through
`TCPServer.server_bind()`, preserves numeric bound-address metadata, and has a
regression that fails if construction calls `socket.getfqdn()`. The adjacent
transfer suites passed 30 tests and the integrated suite at that historical
checkpoint passed 1,304. Later recovery and truthfulness fixes are included in
the final source `8ee8908`, whose integrated suite passed 1,350.

GitHub Actions run `29261331713` against the rejected source was deliberately
cancelled after about five minutes. Its source test, real-Jamulus integration,
and macOS ARM build happened to pass, but the cancelled run is not longevity or
cross-platform evidence and will not be counted. Replacement run `29262915880`
was cancelled after about ten minutes because later fixes changed the source;
it is also **CANCELLED/INSUFFICIENT**. Exact-source run `29269188463` passed the
full one-hour certification and every non-release CI job.

The final fresh-extracted package completed two normal 20-second Host
lifecycles with exit 0 and no forced termination. Bundled Jamulus client/server
3.12.2, UDP, both authenticated RPC listeners, one connected client, and
`caffeinate` were observed in each run; the Qt close timer fired; zero audio
files were created; no child processes or ports remained.

## Historical v0.10 candidate artifact handoff (superseded)

Do not copy the preserved v0.9.0 values into these fields.

```text
Candidate version:       0.10.0
Source commit:           8ee89081802fe5998f71299c4755b21ae5218cb9
Artifact filename:       WebJam-v0.10.0-TEST-NIGHT-macos-arm64.zip
Artifact absolute path:  /Users/jeffstory/Documents/WebJam 2/WebJam-v0.10.0-TEST-NIGHT-macos-arm64.zip
SHA-256:                 f955419909dc014b7172032b00524417983c09e8586c2217691c19838a0b3411
Codesign result:         PASS; strict/deep ad-hoc outer + nested Jamulus/JamulusServer; not Developer ID/notarized
Fresh extraction path:   /tmp/webjam-v010-final-install.gOTCiQ/WebJam.app; strict/deep PASS
Installed test app:      /Applications/WebJam.app; version/build ID/strict/deep PASS
Packaged smoke result:   PASS twice; fresh ZIP, normal 20 s Qt Host/client/server/RPC lifecycle, exit 0, zero audio/leaks, ports free
Failed local report:     /Users/jeffstory/Claude/Projects/WebJam/soak-artifacts/jamulus-jack-soak-native-arm64.json
Native Ubuntu report:    run 29269188463 PASS; 3602.851 measured s; evidence artifact jamulus-jack-soak-29269188463-1
Cancelled runs:          29261331713 and 29262915880 CANCELLED/INSUFFICIENT
Two-Mac worksheet:       NOT RUN
Logic Pro import:        NOT RUN
```

## Historical continuation point

For the current v0.16.2 test build and historical v0.14/v0.13/v0.12 package
baselines, use the README, `TEST-NIGHT-READ-ME.txt`,
[`SUNDAY_TWO_MAC_PILOT.md`](../../SUNDAY_TWO_MAC_PILOT.md), and
[`WEBJAM_V1_LAST_MILE_PLAN.md`](../WEBJAM_V1_LAST_MILE_PLAN.md). The steps
below are retained only to explain what remained after the v0.10 package.

1. Complete the remaining human-confirmed packaged Band Check, Join, recording,
   Studio, support-button, and interface-route checks on the pilot Macs.
2. Put that exact new ZIP on two Macs and complete
   [`SUNDAY_TWO_MAC_PILOT.md`](../../SUNDAY_TWO_MAC_PILOT.md). Preserve failure
   media and the support bundle before changing any variable.
3. Import the exact export into Logic Pro and record the project rate, track
   identities, alignment, references, and result. Until then keep the result
   **NOT RUN**.
4. The exact-source native handoff gate is satisfied, so the default branch may
   receive the source and evidence-only documentation commit. Do not call the
   build physically certified or release-ready until the two-Mac and Logic
   gates pass. Do not tag, notarize, or publish an artifact unless separately
   authorized.
