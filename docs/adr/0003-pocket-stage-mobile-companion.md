# ADR 0003: Pocket Stage native iPhone companion

> **v0.22.3 pre-publication boundary:** the current menu label is part of this
> candidate and is absent from the immutable published v0.22.2 packages.
> Physical owner-device installation and pairing remain **NOT RUN**.

- Status: Accepted; owner-device developer-preview vertical slice implemented
- Date: 2026-07-21
- Scope: Pocket Stage v1 generated native-app developer preview
- Evidence status: Source, generated-project compilation in CI, and live
  Swift-to-Python pinned-WSS automation are present. A distributed iPhone app,
  physical iPhone/Mac pairing, realtime rehearsal use, and acoustic validation
  are **NOT RUN**.

## Context

WebJam already has one authoritative desktop session conductor, a
Jamulus-owned live-music path, host recording, Session Canvas, and a
non-destructive Studio. An iPhone is useful beside an instrument as a focused
second screen and remote, but it must not become another audio engine or a
second source of session truth.

The existing Local Companion API is intentionally loopback-only and read-only.
It is not widened to a LAN control API. Jamulus continues to own the live audio
path, device selection, buffering, and each musician's monitor mix.

## Decision

The repository now contains a narrow **Pocket Stage** developer-preview slice:

```text
iPhone native SwiftUI app
    |  pinned WSS; bounded protocol-v1 messages
    v
dedicated desktop Pocket Stage gateway
    |  immutable anonymous snapshot / semantic commands
    v
existing desktop controllers and authoritative state
    |  existing Jamulus and recorder integrations
    v
Jamulus / host recorder
```

The desktop remains authoritative. Pocket Stage is activated explicitly from
**More → Use iPhone as Pocket Stage…** and is intended only for an iPhone on the same private
Wi-Fi network as that desktop. Choosing it starts a dedicated listener; normal
Host/Join behavior does not start the listener.

This decision records only the implemented vertical slice. Chat, reactions,
rehearsal-plan control, Studio transport, and phone audio remain outside it.

## Pairing and transport

For each explicit sharing session, the desktop generates an ephemeral
self-signed certificate and private key. The pairing QR carries a secure local
WebSocket endpoint, a random bearer capability, its expiry, and the lowercase
SHA-256 fingerprint of the exact leaf certificate's DER bytes.

The iPhone accepts the connection only when the presented certificate matches
that exact DER fingerprint. The pin is not an SPKI/public-key pin and it is not
derived from the Wi-Fi network name. “Same private Wi-Fi” is a reachability
requirement, not authentication.

The desktop issues each QR capability for 120 seconds. It is consumed
atomically by one pairing claim, and replay, reuse, expiry, or replacement with
a new code fails closed. Closing Pocket Stage sharing stops the listener,
disconnects clients, revokes outstanding offers, and destroys the temporary
TLS material.

The developer-preview protocol does not issue a separate reconnect credential.
The accepted capability authorizes the active WebSocket only. After a
disconnect, the supported recovery is to choose **New Code** on the desktop and
pair again; retaining the old QR or a phone-local claim identifier does not
create durable trust.

## Current projection and commands

The desktop publishes a strict, immutable, generation/revision-guarded mobile
projection. It includes the current session role and phase, primary guidance,
recording state, and session-local participant slots. For each slot, the
explicitly paired phone may see a bounded display label plus current fader,
pan, mute, solo, local-slot, and connection state. The label is paired-private
session content; provider IDs, device names, paths, invitations, and credentials
are absent, and labels never enter gateway logs, diagnostics, support bundles,
or the public Local Companion API.

The currently granted actions are deliberately small:

- observe the current session and recording state;
- change fader or mute for a current session-local mix slot;
- add a timestamped “Pocket Stage” marker to Session Canvas;
- when the desktop is the host, Jamulus is connected, and Recording Setup has
  already been completed on the desktop, request host recording start/stop.

Pan remains a bounded snapshot field and reserved protocol command, but v1
does not present or apply it. The pinned Jamulus 3.12.2 client has no supported
pan command, so claiming a live result would violate desktop-authoritative
truth.

Commands carry a unique ID plus the expected session generation and revision.
The gateway rejects missing scope, stale state, invalid bounds, changed
duplicate IDs, and excessive command rates. A button press is not recorder
success: recording requests remain pending until the desktop's existing
recording owner publishes new state.

Solo is visible in the snapshot only. There is no solo command in this slice.
Section transport exists in the versioned protocol vocabulary but is not
granted or applied by the desktop developer preview.

## Deliberate limits

The current developer preview has:

- no live or captured phone audio, video, waveform, or media upload;
- no chat, reactions, or quick responses;
- no solo command;
- no revisioned rehearsal plan, cue advance/rewind, or section transport;
- no Studio playback, transport, editing, save, or export control;
- no durable device trust or reconnect credential;
- no App Store, TestFlight, or packaged WebJam release deliverable.

The checked-in XcodeGen specification generates the native SwiftUI app target,
its local Swift package dependency, and required privacy/network properties.
Running it on an owner's iPhone requires selecting a unique bundle identifier
and Apple Personal Team in Xcode. Personal Team provisioning is a development
path, not a distribution promise.

## Existing boundaries remain unchanged

- The existing Local Companion API stays loopback-only, read-only, separately
  authenticated, and anonymous according to its established contract. Bounded
  paired-private Pocket Stage labels do not alter its public schema.
- Pocket Stage has no HTTP/browser control surface and no plaintext WebSocket
  fallback.
- Jamulus remains the only performance-audio path. Pocket Stage work does not
  run in audio, capture, meter, waveform, playback, or export callbacks.
- Host/Join, Webex, recording validation, take files, and Studio document/export
  ownership do not move to the phone.
- WebJam still cannot infer that musicians heard one another from a connection,
  meter, command receipt, or phone display.

## Consequences

The source tree now has a concrete, testable mobile-control boundary without
expanding the existing localhost API or introducing phone audio. It also adds a
new private-LAN attack surface, an ephemeral TLS lifecycle, a cross-language
protocol, and an owner-device signing path that require independent review.

No physical result is implied by the source implementation. Pairing on an
actual iPhone, local-network permission behavior, certificate handling,
backgrounding, interruption, accessibility, control correctness, Jamulus
non-interference, recording behavior, and long-rehearsal resource use remain
**NOT RUN** until recorded against exact desktop and iPhone builds.
