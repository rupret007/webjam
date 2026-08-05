# ADR 0005: Host-controlled Reference Track as a Jamulus participant

- Status: Accepted; macOS capability is machine-derived and fail-closed;
  physical acceptance remains pending
- Date: 2026-07-27
- Scope: Reference Track v1
- Evidence status: Unit and synthetic integration coverage is present. Physical
  two-endpoint audibility, independent musician mixes, real server recording,
  route removal, and long-rehearsal behavior are **NOT RUN**.

> **v0.22.4 published boundary:** the callback and source-load changes below
> are included in the immutable published v0.22.4 private test candidate.
> Physical audibility and isolation remain **NOT RUN**.

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

That proof was not enabled in production v0.22.2 wiring. A reported CoreAudio
failure can return a process's output device for the input scope after an input
switch. Jamulus 3.12.2 exposes no independent live sound-device RPC, and a
saved profile is only a secondary consistency check. The immutable v0.22.2
backend therefore returned an unavailable capability before device scanning;
BlackHole setup and **Recheck Route** cannot unlock a downloaded v0.22.2
package.

The v0.22.4 candidate changes the capability policy, not the physical
evidence ledger. Production construction derives prerequisite authority on the
Mac: an official, unambiguous BlackHole 16ch/64ch route at 48 kHz may make Play
available. Choosing Play still requires fresh, exact primary/backing PID route
proof, private authenticated RPC, a connected roster, zero return faders, and
unchanged isolation. Missing, stale, or ambiguous evidence emits silence and
fails closed. The explicit boolean constructor argument remains a test-only
override; production does not set it, and no environment variable, setting,
command-line option, or UI action can bypass machine certification. Windows
VB-CABLE/JACK and Linux JACK backends remain unavailable until equivalent
implementations exist.

The source pilot now uses a preallocated single-producer/single-consumer handoff.
The callback performs no mutex acquisition, wait, source I/O, or new audio-buffer
allocation; it pulls into caller-provided output and emits preallocated silence
on underrun. That reviewed source boundary is still not physical scheduling or
allocation proof. Before physical readiness is claimed, instrument callback
timing and allocation behavior on real hardware under load (or obtain
equivalent measured proof at a native callback boundary). Synthetic underrun
tests alone do not certify callback scheduling.

## User controls

Source validation is intentionally independent from route authority. The host
can load and inspect supported WAV/WAVE, AIFF, or FLAC while route capability
is unavailable; MP3 is offered only when the packaged runtime proves decoder
support. Loading decodes the first bounded audio block. Loading and **Recheck
Route** never start playback. In current source, the production Mac factory may
make Play available only after it certifies an official, unambiguous 48-kHz
BlackHole 16ch/64ch route. Choosing Play then performs the exact live isolation
checks; route availability alone is not playback success.

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
- On a machine-certified macOS route, playback begins only after route identity,
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

Once machine-certified, the design gives every musician familiar
per-participant level control and the host one transport without a new network
media plane. The cost is an additional client and strict virtual-route setup.
This is not a substitute for synchronized local playback. Machine-derived
permission to attempt playback is not physical acceptance: isolation, device
switching, exclusivity, audibility, recording, teardown, and recovery evidence
must still be recorded against an exact build and reviewed before those gates
can be reported as passed.
