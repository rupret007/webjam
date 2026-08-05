# Pocket Stage developer-preview threat model

> **v0.22.4 published boundary:** the current menu label is included in the
> immutable published v0.22.4 private test candidate.

- Date: 2026-07-21
- Applies to: implemented Pocket Stage v1 generated native-app vertical slice
- Evidence status: Source defenses, generated-project CI compilation, and live
  Swift-to-Python pinned-WSS automation exist. Physical
  iPhone/Mac, private-Wi-Fi, accessibility, interruption, and realtime rehearsal
  validation are **NOT RUN**.

## Security objective

A musician may explicitly expose a small Pocket Stage surface from their
running WebJam desktop to one iPhone on the same private Wi-Fi. Another LAN
device, webpage, expired QR, replayed claim, or stale phone must not gain broader
desktop, Jamulus, recorder, file, or Studio authority.

Failure must leave the desktop jam authoritative and usable. Pocket Stage loss,
malformed traffic, or overload must not stop, restart, or reconfigure Jamulus,
block recording cleanup, or enter a realtime audio/capture callback.

## Protected assets

- authority over the current monitor mix and host recorder request;
- accurate session, mix, and recording state;
- the one-use QR capability and ephemeral TLS private key;
- bounded participant display labels shown only to an explicitly paired phone;
- invitation material, Jamulus credentials, Webex URLs, local paths, device
  identifiers, provider/channel identifiers, and raw exceptions;
- desktop availability and realtime scheduling headroom.

Chat, reactions, rehearsal plans, Studio state, and phone media are not mobile
assets in this slice because the mobile projection does not contain them.

## Trust zones and boundaries

| Zone | Treatment |
| --- | --- |
| Desktop state owners | Sole authority for session, mix, and recording facts. |
| `core/pocket_stage.py` | Strict protocol, one-use capability, projection, command, and receipt types; no Qt or sockets. |
| Pocket Stage gateway | Untrusted-network parser and WSS boundary; separately owned from the Local API. |
| Private Wi-Fi/LAN | Hostile. Reachability does not establish identity or authorization. |
| Paired iPhone | Authorized only for scopes consumed on the active socket; every message remains untrusted input. |
| Existing Local Companion API | Unchanged loopback-only, read-only surface; not a mobile transport. |
| Jamulus audio path | Separate realtime boundary; never carries Pocket Stage messages. |

Desktop-account or iPhone compromise remains a residual risk. This design does
not protect information already available to malware running as the desktop
user or an unlocked, paired phone.

## Pairing and secret lifecycle

Pocket Stage is off until **More → Use iPhone as Pocket Stage…**. The desktop binds a dedicated
WSS listener to one private IPv4 interface and random port, then creates an
ephemeral self-signed certificate and private key.

The QR contains a random bearer capability with a 120-second expiry. It also
contains the SHA-256 fingerprint of the complete leaf-certificate DER bytes.
The phone must compare the presented certificate to that exact pin; the
self-signed certificate is not accepted merely because it came from the local
network.

The desktop stores only a digest of the plaintext capability, consumes the
capability atomically, and rejects replay, reuse, expiry, revocation, and a
claim with the wrong token. Creating a new code revokes outstanding codes.
Stopping sharing revokes outstanding offers, closes sockets, stops the
listener, and removes the temporary key material.

There is no server-issued or durable reconnect credential in this slice. An
accepted capability authorizes only its active WebSocket. After disconnection,
a fresh QR is required. A phone-local claim ID or cached QR is not an
authentication credential and must not be documented as one.

QR contents are sensitive bearer material until consumed or expired. They must
not enter normal logs, diagnostics, support bundles, analytics, process
arguments, clipboard conveniences, or accessibility descriptions.

## Current authorization matrix

| Scope | Current data/actions |
| --- | --- |
| `observe` | Session role/phase/guidance, recording state, and session-local slots with bounded paired-private display labels. |
| `mix` | Set fader or mute for a current session-local slot. Pan is reserved but rejected until a proven provider path exists. |
| `markers` | Append one bounded, session-timestamped marker to Session Canvas. |
| `record` | Host-only start/stop request after desktop setup and live connection. |

The desktop grants `record` only for a host session. It still rejects a record
request unless hosting is enabled, Jamulus is connected, and the first-record /
Local Originals choice has already been made on the desktop.

There is no chat or reaction action, solo command, rehearsal-plan command,
section transport grant, Studio command, generic RPC, filesystem argument,
shell text, or arbitrary callback name. Participant display labels are bounded
paired-private content, not command identities and not public diagnostics.

## Protocol and command invariants

- Only WSS is exposed; there is no plaintext WebSocket or browser-readable
  control route.
- Messages are versioned, strict-key parsed, size-bounded, sequence-checked, and
  rate-limited. Unknown fields and unsupported message kinds fail closed.
- The projection is immutable and carries a session generation plus semantic
  revision. Commands with stale generation or revision are rejected.
- Commands use a finite enum and bounded typed arguments. IDs are idempotent in
  a bounded receipt cache; reusing an ID with different content is rejected.
- The gateway limits clients, command rate, frame size, queues, and cached
  receipts. Slow or malformed clients can be disconnected.
- Raw peer input and provider exceptions are not returned to the phone or
  written as ordinary gateway log content.
- Snapshot publication and command application are outside audio, recording
  capture, meter, waveform, playback, and export callbacks.

Command receipts distinguish `accepted`, `confirmed`, `pending`, and
`rejected`. Jamulus mixer RPC is fire-and-forget, so a fader/mute request is
only `accepted` by the desktop and the following full snapshot reconciles its
local state; it is never described as provider-confirmed. A host record request
is pending while the recording owner transitions; the phone cannot claim a
completed take from request acceptance.

## Threats and current mitigations

| Threat | Current mitigation | Residual risk / required evidence |
| --- | --- | --- |
| QR theft or enrollment race | Random one-use capability, two-minute expiry, atomic consume, New Code revocation | Someone who can see the live QR may win the race; physical UX is **NOT RUN**. |
| LAN eavesdropping or MITM | WSS plus exact leaf-certificate DER SHA-256 pin; no plaintext fallback | Endpoint/timing metadata remain visible; automated Swift TLS pairing passes, but physical iOS behavior is **NOT RUN**. |
| Replayed or stale request | Consumed capability, per-socket sequencing, generation/revision checks, bounded idempotency cache | Disconnect recovery needs a fresh QR; interruption behavior is **NOT RUN**. |
| Malformed/flooding client | Strict parser, 64 KiB frame bound, client/rate/queue/cache limits, disconnect policy | CPU/memory behavior during a real rehearsal is **NOT RUN**. |
| Wrong participant controlled | Session-local slot is resolved again on the desktop UI thread and unavailable slots reject | Roster churn with real Jamulus peers is **NOT RUN**. |
| Unauthorized recording | Host-only scope plus hosting, connection, setup, state, generation, and revision checks | Real start/stop/finalize behavior from iPhone is **NOT RUN**. |
| UI/network failure affects music | Separate gateway ownership; no phone audio; no work on realtime callbacks | Long-session dropout/underrun measurement is **NOT RUN**. |
| Broader data disclosure | Session-local slot IDs, bounded paired-private labels, finite state, and no chat/paths/URLs/device IDs/raw errors; labels are excluded from logs/diagnostics/public API | Screen capture of intentionally displayed paired content remains possible. |
| OS firewall or local-network denial | No automatic rule changes or admin escalation; the UI gives permission-specific recovery guidance | macOS Application Firewall, Windows Private-network permission, and Ubuntu firewall allow/deny/recovery are physical **NOT RUN** gates. |
| VPN, multiple private interfaces, DHCP, or sleep/wake | Listener binds one explicit RFC1918 address and never wildcard/public | The preview has no interface picker or automatic certificate/listener rotation; create a fresh sharing session after network changes. Physical recovery is **NOT RUN**. |
| Pre-WebSocket slow or excess peers | The application protocol bounds authenticated sockets, frames, queues, rates, and receipts | OS/Uvicorn pre-upgrade connection behavior remains a bounded-load physical/hostile-LAN evidence gap. |

## Deliberately absent attack surfaces

The developer preview does not implement phone audio capture/streaming, chat,
reactions, solo control, rehearsal plans, Studio
transport/editing/export, media upload, Internet relay, remembered-device trust,
or a reconnect credential. Adding any of these requires a new data-flow and
authority review before implementation.

The existing Local API and Jamulus audio path are unchanged. Pocket Stage must
not be used as evidence that those boundaries, human audibility, or production
distribution have been physically certified.

## Validation status

Automated tests cover protocol validation, capability lifecycle, gateway
bounds, TLS identity/fingerprint generation, controller command routing, the
real Swift transport against the live pinned-WSS gateway, and unsigned iOS app
compilation. That evidence does not prove an installed iPhone can pair with an
installed desktop build or that a musician can operate it safely during a
rehearsal.

All physical rows remain **NOT RUN**: real QR pairing, wrong/expired pin,
Camera and Local Network permission, macOS/Windows/Ubuntu firewall denial and
recovery, phone lock/background/return, Wi-Fi/VPN/IP change and computer
sleep/wake, fresh-code recovery, paired-label privacy, mix correctness, host
recording, VoiceOver/Dynamic Type, resource use, and non-interference with
two-way Jamulus audio.
