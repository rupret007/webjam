# ADR 0005: Host-controlled Reference Track as a Jamulus participant

- Status: Accepted; engine implemented, production playback locked pending
  physical macOS certification
- Date: 2026-07-27
- Scope: Reference Track v1
- Evidence status: Unit and synthetic integration coverage is present. Physical
  two-endpoint audibility, independent musician mixes, real server recording,
  route removal, and long-rehearsal behavior are **NOT RUN**.

> **Unreleased after v0.22.2:** callback and source-load details below describe
> source changes not present in the immutable published v0.22.2 packages.

## Context

Bands often rehearse to a demo, click-free guide, or finished song. Playing the
file directly through the host's speakers would create feedback and would not
give remote musicians an independent level. Sending synchronized local copies
to every musician would require a new media-distribution protocol, shared
clock, drift correction, and latency model.

Jamulus already supplies the performance path and gives every connected
participant a separate mixer channel. Its supported routing model is to feed
computer audio into another Jamulus client. WebJam therefore treats the song
as one more musician rather than as a Studio take or a second monitor output.

## Decision

Reference Track v1 is host-only and uses a dedicated WebJam-owned Jamulus
client:

```text
bounded 48 kHz decoder/stream
        |
        v
isolated virtual input route
        |
        v
WebJam Track Jamulus client ----> hosted Jamulus server
        |                              |
        | all return levels zero       +--> one mixer participant per musician
        +-- no physical/direct output  +--> one server-recording stem
```

The feature exposes an immutable `ReferenceTrackSnapshot`, a finite state
machine, and a platform `ReferenceAudioBridgeBackend`. The controller owns
file validation, bounded decoding, transport intent, state transitions, and
fail-closed cleanup. Qt only renders snapshots and emits semantic controls.

The retained backend is a macOS 14.2-or-later source pilot for a BlackHole
route. It resolves both the owned primary Jamulus PID and the separately owned
backing PID to CoreAudio Process AudioObjects. The primary must have exactly
one actively running physical input and output and must not use BlackHole. The
backing client must use the exact selected BlackHole device for both directions.
WebJam repeats the combined proof during playback and silences the callback
whenever it is absent, changed, ambiguous, or stale.

That proof is not enabled in production v0.22.2 wiring. A reported CoreAudio
failure can return a process's output device for the input scope after an input
switch. Jamulus 3.12.2 exposes no independent live sound-device RPC, and a
saved profile is only a secondary consistency check. Until the exact physical
pilot proves device-switch truth, BlackHole exclusivity, and no direct monitor,
the production backend returns an unavailable capability and `prepare()`
independently refuses all native work. Production capability refusal occurs
before device scanning, so BlackHole setup and **Recheck Route** cannot unlock
a downloaded v0.22.2 package.

The certification seam is an explicit boolean constructor argument used only
by controlled tests or an instrumented source pilot. Production construction
leaves it false. No environment variable, setting, command-line option, or UI
action can change it. Windows VB-CABLE/JACK and Linux JACK backends likewise
stay unavailable until they receive equivalent physical evidence.

The source pilot now uses a preallocated single-producer/single-consumer handoff.
The callback performs no mutex acquisition, wait, source I/O, or new audio-buffer
allocation; it pulls into caller-provided output and emits preallocated silence
on underrun. That reviewed source boundary is still not physical scheduling or
allocation proof. Before the production lock is removed, instrument callback
timing and allocation behavior on real hardware under load (or obtain equivalent
measured proof at a native callback boundary). Synthetic underrun tests alone do
not certify callback scheduling.

## User controls

Source validation is intentionally independent from route authority. The host
can load and inspect supported WAV/WAVE, AIFF, or FLAC while route capability
is unavailable; MP3 is offered only when the packaged runtime proves decoder
support. Loading decodes the first bounded audio block. Loading and **Recheck
Route** never start playback. Production Play remains locked before route
scanning until physical route and isolation evidence is accepted into a future
release.

Once route evidence is certified, the host can play, pause, restart, seek while
paused, set a loop range, apply bounded source trim, and add an audible
count-in. WebJam does not persist the file path or represent the file as a
recorded take.

The UI says **Jamulus-routed**, never “latency eliminated” or “synchronized.”
The track receives Jamulus's normal buffering, jitter handling, and network
latency like another participant.

## Isolation and lifecycle invariants

- The backing client has its own native profile, client RPC port, secret, and
  process ownership. Each start reserves unpredictable, session-unique private
  profile and secret names beneath one descriptor-pinned directory. It does
  not reuse the musician client's lifecycle.
- All eligible BlackHole variants share one WebJam-owned lifecycle claim. The
  process-local claim is reinforced by an interprocess lock and a kernel
  loopback socket inherited by the backing Jamulus child. A second WebJam
  process cannot start another Reference Track while the first route or an
  orphaned backing child still holds that socket. This coordinates WebJam
  owners; it does not claim to exclude an unrelated same-user audio
  application.
- In a controlled certified pilot, playback begins only after route identity,
  separate-client ownership, RPC control, live PID-bound primary and backing
  route isolation, and zero return levels are proven. Saved profile device
  names are only a secondary consistency check. On macOS the verified
  non-sandboxed integrated client resolves its filename-only profile in
  WebJam's private Application Support launch directory. Primary CoreAudio
  process-route proof remains authoritative. The separately owned Reference
  Track client keeps descriptor-pinned ownership and cleanup for its private
  profile, with pre/post launch path validation, and does not weaken that
  boundary.
- The backing client's return uses separate BlackHole channels and has no
  physical monitor route. The host must hear the track only through the primary
  Jamulus mix.
- If route health, RPC health, Jamulus connection, host role, or isolation
  proof is lost, changed, ambiguous, or stale, playback emits silence and
  stops. Uncertain state is failure, not “playing.”
- End/Leave and application shutdown stop the Reference Track before the
  primary musician client or hosted server.
- Cleanup retains the process, RPC, descriptor-pinned files, and route lease
  until every step is proved. Jamulus may atomically rewrite its profile on
  exit; WebJam removes that replacement only after bounded XML validation
  proves the expected participant, device, and channel identity. A failed
  cleanup remains visible and retryable through **Stop**, and blocks source
  replacement and shutdown instead of reporting success.
- The legacy Webex audience bridge and Reference Track cannot claim the same
  virtual route at the same time.
- A hosted recording captures the dedicated participant as its own stem; the
  UI warns the host before playback.

## Privacy and error handling

Only a sanitized source filename may appear in the private UI snapshot. The
source path is memory-only and is excluded from settings, logs, metrics,
diagnostics, support bundles, recovery records, and backend command output.
Public diagnostics use a strict allowlist of finite playback/source/route
state, source format/rate/channels/duration, platform, backend, and route reason
without a filename or free-form text. Errors are bounded, musician-facing
messages without raw paths, process arguments, secrets, or provider output.

## Consequences

Once certified, every musician gets familiar per-participant level control and
the host has one transport without a new network media plane. The cost is an
additional client and strict virtual-route setup. This is not a substitute for
synchronized local playback. The production lock cannot be removed until
physical isolation, device switching, exclusivity, audibility, recording,
teardown, and recovery evidence is recorded against an exact build and the
evidence is reviewed.
