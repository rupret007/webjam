# ADR 0001: Remote session transport

- Status: Accepted
- Date: 2026-07-13
- Scope: WebJam v0.11.0 remote pilot, one host plus one guest

## Context

WebJam v0.10.0 has a certified same-LAN Jamulus and recording workflow. Its
v1/v2 invitation carries a literal LAN endpoint and its private recording plane
is bounded plaintext HTTP on RFC1918 IPv4. Neither may be exposed to the public
Internet.

The v0.11.0 pilot needs one private link that works across ordinary musician
networks without router changes, VPN accounts, Terminal, elevated privileges,
or networking choices. Jamulus 3.12.2 remains unmodified and remains the only
live-music engine. The new layer must carry Jamulus's latency-sensitive UDP
packets, reliable control and original-media data, authenticated end-to-end
encryption, direct traversal, relay fallback, reconnect, and bounded evidence.

## Decision

Use a small, statically compiled Go sidecar built from pinned Pion ICE/TURN and
quic-go dependencies.

Each desktop starts exactly one owned sidecar for a v3 session. Jamulus talks
only to a random loopback UDP port owned by that sidecar. The guest sidecar
carries each Jamulus packet as a QUIC DATAGRAM over the ICE-selected path. The
host sidecar forwards it from a peer-specific loopback UDP socket to the
loopback-bound Jamulus server, preserving Jamulus participant identity. Replies
follow the inverse path. The host's own Jamulus client continues to use the
server loopback endpoint directly.

The same QUIC connection provides separately framed, bounded reliable streams
for session control and resumable original-media transfer. Media streams are
paced and yield to live datagrams; they pause under live-path backpressure.
Application recording, participant, take, alignment, Studio, and Logic models
remain canonical and outside the sidecar.

The transport core uses Pion ICE for direct and TURN paths, with candidate
signaling authenticated before ICE. Domain-separated keys derived from the
invitation capability bind each canonical candidate bundle to the session,
roles, peer certificate pins, nonce, generation, and expiry. A stale, replayed,
modified, cross-session, cross-role, or unsigned bundle is rejected before any
candidate address is attempted. Candidate envelopes are AEAD-sealed so the
rendezvous cannot inspect them.

The repository's reference service is a separate native relay proof: it
exchanges bounded sealed control payloads and exposes an authenticated
exact-pair UDP wrapper as a fixed-peer `net.PacketConn` for QUIC. It is not a
TURN server and does not issue TURN credentials. Pion direct/TURN behavior and
the native reference relay therefore remain distinct test paths until a future
public profile deliberately selects and proves one production topology.

On a Pion path, ICE prefers a viable direct pair and uses TURN when direct
traversal is unavailable. On the native reference path, the selected route is
the exact-pair relay. WebJam records the selected path and measured health as
musician-safe transport evidence. A restart creates a new connection
generation; QUIC 0-RTT is disabled. WebJam does not migrate an active recording
unless generation, ordering, duplicate, timeline-gap, identity, and transfer
invariants have all been proven.

The v0.11.0 vertical slice is deliberately limited to one host and one guest.
The protocol carries an explicit participant limit so a later release can raise
it without weakening allocation, identity, or resource bounds.

## Version and process boundary

The desktop owns a `SessionTransportCoordinator`; it does not put network state
into `ApplicationController`. The coordinator chooses one implementation:

- `LanDirectTransport` for existing valid v1/v2 same-LAN sessions;
- `RemoteSessionTransport` for v3, backed by the sidecar;
- explicit direct or relay evidence within the remote implementation.

Python and the sidecar exchange a strict, versioned, length-bounded command and
event protocol through inherited standard pipes. Session secrets enter through
stdin, never command-line arguments or environment variables. Sidecar stdout
contains allowlisted events and public identifiers only. stderr is bounded and
redacted before retention. A single reader worker publishes immutable snapshots
to Qt through the existing queued UI-thread invoker. Per-packet IPC is avoided:
Jamulus packets stay in the sidecar's loopback UDP/QUIC data path.

The sidecar binds loopback only for desktop IPC and Jamulus proxying. It has
bounded queues, datagram size, stream count, transfer chunk, bandwidth, retry,
timeline, and shutdown budgets. WebJam owns and reaps the process and its pipes,
ports, allocations, session directory, and stale-session sweep.

## v3 enrollment and authentication

The link shape remains `webjam://join?v=3&r=<profile>&i=<opaque-envelope>`.
`r` is an application-shipped profile identifier, not a URL. The allowlisted
profile maps to a fixed transport/service configuration; the current
`reference-local` profile fixes native control and exact-pair relay endpoints
to loopback and is lab-only. The opaque binary
envelope contains only a protocol marker, random logical-session and
invitation references, one-use capability, expiry, participant limit, and host
ephemeral certificate fingerprint. It contains no musician name, address,
port, path, relay credential, or persistent account identifier.

The host creates a session-scoped Ed25519 identity and short-lived certificate.
The guest pins its fingerprint from the invitation. The guest creates its own
session-scoped identity. Before QUIC, the guest presents the service with a
session-bound token derived from the invitation capability. The service
atomically consumes that token at `Enroll`, admits one guest role, and rejects a
second enrollment. The guest then sends an AEAD-sealed certificate/pin
bootstrap through bounded reference-control `signal`/`poll`; the host validates
it and returns an AEAD-sealed acknowledgment through the same opaque queue.

The guest pins the host during QUIC TLS 1.3. The host initially accepts only a
bounded valid ephemeral client certificate. All application datagrams and
reliable streams remain gate-closed while each side exchanges a
capability-authenticated, TLS-exporter-bound proof through reference control.
Each side verifies the remote proof, the exact TLS peer SPKI, both pins,
session, generation, expiry, and nonce before starting peer pumps. There is no
QUIC enrollment stream in this reference path and no proxy-only success event.

The service-side enrollment token is consumed before the TLS/exporter proof,
not after it. A bearer thief can therefore enroll first and abandon the proof,
burning the invitation as a denial of service; Reset Invite revokes that
service session and rotates the invitation reference and capability. The
current slice does not issue a reconnect credential or reuse a consumed
invitation. Reconnect requires a fresh invitation and generation. An old,
expired, reset, cross-session, concurrent, downgraded, or already-consumed
enrollment is rejected.

HKDF-SHA256 derives independent reference-service enrollment,
candidate-signaling, bootstrap, acknowledgment, and proof values from the
256-bit capability. The reference service receives the session-bound derived
enrollment token and opaque signaling payloads, never the raw capability or
proof key. Domain labels and transcript fields are versioned constants and
have cross-purpose/cross-session tests.

Session identities remain in sidecar memory. Reset Invite retains the prepared
host identity so the already displayed host pin remains valid, while sidecar
shutdown destroys it. Identity keys are never written to ordinary app settings,
logs, manifests, support bundles, filenames, crash text, metrics, or clipboard
diagnostics.

The invitation is a bearer secret until service enrollment, reset, or expiry.
Anyone who obtains an unused link can race the intended guest; this is an
explicit residual risk of account-free one-link enrollment. A thief may consume
enrollment without completing peer proof and force a Reset Invite. Short expiry,
one guest, host Reset Invite, visible roster truth, and revocation bound that
risk.

## Rendezvous and relay boundary

The repository includes a containerizable reference service with in-memory
session state by default. It provides health, opaque registration/enrollment,
bounded sealed signaling, expiration, revocation, bandwidth/accounting
evidence, and an authenticated exact-pair UDP relay. The relay wrapper has no
destination field: it can forward only between the registered host and guest
endpoints for the exact opaque session and generation. It enforces packet,
byte, replay, rate, datagram-size, idle, and lifetime bounds and fails closed.

The relay observes timing, packet size, volume, and network
source/destination metadata, but QUIC keeps Jamulus, control, and media
plaintext end-to-end protected. The service does not decode or persist
audio/media, store musician names, log capabilities or raw peer addresses,
accept arbitrary proxy destinations, or survive indefinitely after idle/TTL
expiry.

The local/CI reference may use plaintext TCP NDJSON only while all listeners
remain on loopback. Any external pilot requires a stable DNS name, trusted TLS
1.3 protection for control, reachable native relay UDP, firewall/egress policy,
monitoring, capacity/abuse response, secret rotation, and a privacy review. A
future stock-TURN topology would additionally require its own exact-pair
authorization and packet-policy proof; the reference service must not be
presented as TURN. No public service or production credential is created by
this goal.

## Measured prototypes

All figures below came from isolated `/tmp` prototypes on the development Mac.
They informed the decision but do not count as committed, packaged, WAN,
physical, or acoustic proof.

### In-process aioquic

Pinned aioquic 1.3.0 plus cryptography 49.0.0 occupied 22 MiB across 466 files.
Direct loopback QUIC completed a handshake in 10.801 ms and echoed 500
200-byte datagrams with zero loss at 0.141 ms p50 / 0.394 ms p95. An opaque UDP
forwarder completed the relayed handshake in 6.915 ms and echoed 500 datagrams
with zero loss at 0.168 ms p50 / 0.226 ms p95. It forwarded 1,131 encrypted
datagrams without observing a plaintext sentinel. An 8 MiB reliable-stream
echo sustained 8.13 MiB/s. A ten-second 2,000-datagram run delivered zero loss
at 198.2 round trips/s with 1.076 ms p50 / 1.601 ms p95, approximately 15.45%
of one CPU core for both endpoints and relay in one Python process, and about
40.1 MiB maximum RSS.

This validated QUIC DATAGRAM plus reliable streams and the opaque-relay
boundary. It did not provide ICE traversal or TURN allocation; adding those in
Python would create a new traversal implementation and a larger native Python
packaging/audit surface.

### Go ICE/QUIC sidecar

Pinned Go 1.25.12, Pion ICE v4.3.0, Pion TURN v5.0.12, Pion transport v4.0.2,
and quic-go v0.60.0 compiled with `CGO_ENABLED=0`, `-trimpath`, and stripped
symbols. A fixed-peer `net.PacketConn` adapter over Pion ICE successfully
carried QUIC TLS 1.3, QUIC DATAGRAM, and a concurrent reliable stream.

In the deterministic virtual network:

- direct ICE connected in 602.4 ms and QUIC handshook in 1.46 ms;
- 500 alternating 440/660-byte direct datagrams had zero loss, 0.081 ms p50,
  and 0.255 ms p95;
- TURN relay connected in 0.278 ms and QUIC handshook in 0.891 ms;
- 500 relayed datagrams had zero loss, 0.072 ms p50, and 0.441 ms p95;
- direct and relay paths each SHA-verified a concurrent 16 MiB stream;
- with 5 ms minimum delay, 2 ms jitter, and deterministic loss of every 20th
  client QUIC packet, the live plane delivered exactly 475/500 probes while
  the reliable stream recovered and SHA-verified. The slow bulk transfer under
  loss is the reason pacing/backpressure is a release requirement.

Five repeat runs per path had zero datagram loss. Median direct/relay ICE
connect was 603.318/0.374 ms, QUIC handshake 1.147/1.131 ms, datagram p50
0.048/0.078 ms, p95 0.130/0.243 ms, and a 1 MiB stream took 38.437/39.910 ms.
The consistent direct ICE delay is Pion's deterministic srflx nomination timing,
not simulated path latency. One relay connect outlier was 201.5 ms; the other
four were below 0.5 ms.

Under the same impairment through TURN, 284/300 live probes arrived, p50 was
130.6 ms and p95 297.3 ms, while a concurrent 4 MiB stream SHA-verified in
25.57 seconds. An 8 MiB relay stream exceeded the bounded 45-second window.

Static stripped prototype builds succeeded at these raw/gzip sizes:

| Target | Raw bytes | gzip bytes |
| --- | ---: | ---: |
| macOS arm64 | 8,113,522 | 3,205,025 |
| macOS x64 | 8,751,312 | 3,484,700 |
| Windows x64 | 8,760,320 | 3,477,236 |
| Linux x64 | 8,614,072 | 3,454,454 |

The module graph contained 33 modules; 15 non-standard modules were linked.
Representative 500-probe plus 16 MiB runs used 19,349,504 bytes direct and
20,054,016 bytes relayed maximum RSS for both simulated peers, TURN, and vnet
inside one process.

These virtual-network latencies are implementation overhead measurements, not
geographic network latency.

quic-go also reported that it could not resize the receive buffer on the
generic adapter. Production must create and tune the underlying UDP socket
before passing it to Pion `UDPMux`; this is an implementation gate because the
adapter bypasses quic-go's usual socket-buffer tuning.

## Options considered

### Expose Jamulus UDP directly

Rejected. It requires public addressing/router work, exposes the server, does
not provide invitation enrollment or secure control/media, and cannot meet the
normal-musician experience.

### Preserve LAN v1/v2 for all sessions

Preserved only as `LanDirectTransport`. It is the lowest-complexity and
lowest-overhead path on a valid same LAN, but RFC1918 invitations and plaintext
peer HTTP are not an Internet architecture.

### External VPN or full userspace overlay

Rejected for this slice. A separate VPN account/client/topology violates the
one-link mental model. Embedding a general overlay adds address/routing/DNS and
privilege surface that WebJam does not need. A future audited userspace overlay
could be reconsidered if UDP/QUIC is widely blocked.

### WebRTC media/data stack

Rejected. WebJam does not need a second audio codec, mixer, jitter buffer, or
participant engine. Using the full WebRTC media stack beside Jamulus would add
competing audio semantics. ICE is reused narrowly for traversal; QUIC provides
the needed unreliable and reliable encrypted channels.

### In-process Python QUIC

Rejected for v0.11 despite good loopback measurements. aioquic proved the data
plane but supplies no complete ICE/TURN traversal layer. Filling that gap would
mean either homegrown traversal or another large dependency family in the Qt
process. It also couples packet processing, native cryptography packaging, and
failure/resource behavior to the UI runtime.

### Static Go sidecar

Selected. It is a small unprivileged process, has a static cross-platform build,
keeps the packet loop away from Qt/Python, combines mature ICE/TURN and QUIC
implementations, gives a narrow kill/rollback boundary, and was the only option
prototyped end to end across direct and relay candidates with both datagrams and
streams.

The cost is a second owned process, a Go build and dependency/license pipeline,
strict IPC/process-lifecycle work, and three packaged binaries. Those costs are
bounded and testable.

## Latency and congestion implications

The proxy adds two loopback UDP hops plus QUIC framing/encryption. Direct paths
avoid relay geography; relay paths add the relay network detour. The prototype
shows sub-millisecond local implementation overhead but does not predict home
Internet suitability. Band Check therefore reports measured path latency,
jitter, loss, and stability and never equates connectivity with playability or
human audibility.

QUIC DATAGRAM is encrypted and congestion-controlled but deliberately not
retransmitted. That matches live Jamulus packets better than a reliable stream.
Reliable control/media streams share congestion state and can delay themselves
under loss, so original transfer is bounded, paced, cancellable, and subordinate
to live audio.

## Packaging and licensing

CI pins the Go toolchain and module graph, verifies checksums, runs Go unit/race
and protocol tests, builds with `CGO_ENABLED=0`, strips the peer binary, and
stages the correct macOS arm64, macOS x64, or Windows x64 executable in the
PyInstaller bundle. Startup verifies the sidecar protocol/build identifier and
fails closed on a missing package SHA-256 manifest, unsafe owner/mode,
wrong-architecture, invalid native signature, hash/build-ID mismatch,
incompatible protocol, or unexpectedly exiting binary. Frozen builds ignore
environment path/build-ID overrides and use only the sibling executable. The
private macOS test build's ad-hoc signature detects damaged or incompletely
staged bundles, but it is not a trusted publisher identity; Developer ID
signing/notarization remains a separate distribution gate.

Pion ICE, Pion TURN, Pion transport, and quic-go use permissive MIT licensing.
Their exact versions, transitive module graph, license texts, notices, SBOM, and
vulnerability audit become package gates before release.

Relevant primary specifications and implementation documentation:

- RFC 8445, Interactive Connectivity Establishment;
- RFC 8656, Traversal Using Relays around NAT;
- RFC 9000/9001/9002, QUIC transport, TLS, loss detection and congestion;
- RFC 9221, QUIC unreliable datagrams;
- Pion ICE/TURN package documentation;
- quic-go QUIC and DATAGRAM documentation.

## Rollback

The remote implementation is behind the versioned transport factory. Disabling
v3 or removing the sidecar returns the app to the preserved v1/v2 same-LAN path
without changing participant, recording, take, Studio, or Logic data. A failed
or exhausted v3 session never opens public Jamulus or falls back to plaintext
Internet transfer; it stops with one useful musician-facing action.

## Required proof before v0.11.0 release

- Strict v3 parsing, expiry, consume, replay, reset, revocation, downgrade,
  concurrent enrollment, cross-session, restart, and redaction tests.
- Direct, forced-relay, fallback, interruption, ICE restart, backpressure,
  malformed-frame, overload, TTL, quota, and cleanup tests in the impairment
  lab.
- A real Jamulus 3.12.2 host and guest with distinct decoded signals through
  direct and forced-relay paths, including reconnect and secure resumed media.
- Packaged sidecar architecture/import/start/stop/deep-link verification on all
  three desktop targets.
- Resource and encrypted-fabric soak evidence, container health/abuse evidence,
  threat/privacy review, SBOM/license/vulnerability audit, and exact full gates.
- Public deployment and two-home physical/acoustic evidence remain explicitly
  **NOT RUN** until actually performed.

## Current committed reference proof

The native reference and IPC integration packages run against an independently
spawned Python service process. Two production runner instances establish the
host-waiting boundary, one-use guest enrollment, capability-sealed bootstrap
and acknowledgment, mutually pinned TLS, bidirectional exporter proofs,
quarantine before both proofs, peer pumps, bidirectional live payloads through
the exact-pair relay and loopback Jamulus proxy seam, invitation reset, close,
and bounded shutdown.

The Go runners execute in the test process and their Jamulus boundary is a
controlled UDP socket. This is stronger than an in-memory protocol model, but
it is not two packaged sidecar executables, two real Jamulus processes, a
public/ordinary-home network, a secure original-media transfer, or an acoustic
result. Those required gates remain open above.

## Platform activation boundary

A v3 bearer must never enter process command-line arguments. Packaged macOS
accepts the existing Qt file-open URL event, and every platform supports paste
into the one password-style invitation field. The legacy `sys.argv` fallback is
v1/v2-only and must explicitly reject v3. Windows click-to-join remains
paste-only for this slice unless a separate activation channel can prove that
the URL never appears in a process command line. This is a truthful platform
limit, not permission to weaken the secret boundary.

Likewise, v3 must not put the musician name in Jamulus `--clientname` process
arguments. It uses the isolated Jamulus configuration adapter or authenticated
post-launch name RPC. Remote-host mode adds `--serverbindip 127.0.0.1`; the
existing LAN mode remains transport-aware so it does not lose LAN access.
