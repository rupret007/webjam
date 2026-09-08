# WebJam fabric

`webjam-fabric` is the statically compiled, unprivileged transport process for
v3 remote sessions. It owns loopback Jamulus UDP proxying, authenticated QUIC
transport, bounded live datagrams, and bounded reliable frames. The
desktop remains the owner of participants, recordings, takes, Studio state,
and user-visible decisions.

This slice provides the process/IPC boundary, loopback proxy, native reference-
service control and exact-pair relay clients, TLS 1.3 certificate pinning,
exporter-bound peer authentication, framing, peer pumps, and deterministic
direct/relay laboratory tests. The compiled `reference-local` profile is
loopback-only and lab-only. No public rendezvous, relay, production TURN
authorization, or user-configurable endpoint is included.

Candidate signaling is AEAD-wrapped with a capability-derived,
domain-separated key and authenticated against session, role, known peer SPKI
pins, nonce, generation, and expiry before candidates enter ICE. QUIC uses ALPN
`webjam/3`; the guest pins the host, presents its own bounded self-signed
ephemeral Ed25519 certificate, and proves that exact guest key during
enrollment. Live datagrams and reliable streams remain quarantined until a
TLS-exporter-bound enrollment proof authorizes the session generation.
The v3 ICE wrapper accepts only public-unicast server-reflexive candidates or
relay candidates whose public address appears in the profile's exact fixed
allowlist; host, private, reserved, and documentation addresses are rejected.

IPC version 1 supports `prepare_host`, one-shot `open_peer` enrollment, and
`send_help` after mutual peer proof. Enrollment accepts only fixed-size,
canonical unpadded-base64url session, invite, capability, and SPKI values plus
the exact compiled `reference-local` profile ID. That profile maps to fixed
loopback native control and exact-pair relay endpoints inside the binary; IPC
cannot supply an address, URL, credential, certificate, key, path, or
free-form network setting. The host private identity never leaves the process;
`host_prepared` / `identity_ready` emits only its SHA-256 SPKI pin.

Help text is the sole bounded free-form IPC value. It is NFC-normalized plain
UTF-8 (500 bytes maximum), rejects markup and control/private-use characters,
and travels only on the existing reliable QUIC plane after that plane is
authorized. Role, generation, and monotonic request ID are checked at both
ends; sending and receiving have bounded token buckets, replay rejection, and
at most eight pending receipts. `help_accepted` means the local transport
accepted the send, while `help_delivered` means only that the peer transport
acknowledged it—not that a person saw or read it. Text and all three help-event
types are excluded from the desktop diagnostic timeline. Closing, resetting,
expiry, or a generation change destroys the channel and its pending state.
There is no persistence, transcript, offline delivery, attachment, analytics,
notification, or separate network service.

The raw invitation capability remains inside the sidecar's peer-authentication
boundary. A distinct session-bound enrollment token is derived with
HKDF-SHA256 for reference-service Register/Enroll, so the service never receives
the raw capability. Host and guest role tokens are independent random values.

Host `open_peer` emits correlated `host_registered` / `host_waiting` only after
the synchronous service registration succeeds. The host later emits exactly
one unsolicited `peer_connected` / `connected` event (`id=0`) only after mutual
TLS, exact certificate pins, bidirectional exporter proofs, and peer pumps are
ready. A guest emits its correlated `peer_connected` event only at the same
authenticated boundary. There is no proxy-only success event. `peer_closed` /
`closed` confirms bounded local teardown; closing a host peer preserves only its
prepared ephemeral identity inside the sidecar (including its private key) so
the existing public pin remains valid for Reset Invite. Shutdown destroys that
identity.

Invitation admission and established-session duration are separate bounds. The
existing default admission window remains ten minutes. Native bootstrap, mutual
peer proof, and the room handshake run under that setup deadline. Successful
admission cancels only the setup child context; the application pumps then use
the separately owned operation context. Promotion is checked before launching
those pumps, not when a later buffered `peer_connected` event reaches the runner.
The operation's maximum lifetime is eight hours, clamped to its actual ephemeral
certificate expiry. A prepared host identity may be older than the operation,
so it can have less time left. Parent cancellation, explicit close/reset,
failure, and shutdown can end it earlier.

The reference service keeps its default 600-second admission and 90-second idle
bounds. Once enrolled, its independent hard ceiling is eight hours from original
registration; enrollment and subsequent traffic cannot extend that ceiling.
Service enrollment is not proof of native mutual authentication. Register/Enroll
response fields still describe admission TTL, not a renewed live-room lifetime.
See [the service protocol](../reference_service/PROTOCOL.md) for the bounded
downward configuration and erasure rules.

Local teardown cancels and interrupts owned media/control workers and joins them
before reporting completion. A bounded local Close failure retains the operation
and identity for retry, fences stale callbacks and application actions, and
blocks opening a second operation. It cannot emit a successful `peer_closed`
receipt or destroy an identity still owned by unfinished work.

The original service-control TCP connection can close after 30 idle seconds
while the room continues over QUIC. Host cleanup therefore makes one fresh
authenticated `reference-local` close attempt with its existing role token,
generation, and next control sequence. Connection and acknowledgment share the
bounded shutdown budget. This adds no enrollment, public address, or reconnect
loop. A failed/uncertain reply leaves remote removal unconfirmed: the local
`peer_closed` receipt does not imply a service-removal acknowledgment, and service
idle/hard lifetime limits remain in force.

`peer_connected` proves the authenticated fabric and running pumps. It does
**not** prove public reachability, a real-home NAT path, suitable latency, a
Jamulus roster, live signal, or human audibility. Pion direct/STUN/TURN paths
remain separate deterministic virtual-network evidence; the native reference
relay is not TURN. ICE restart, address renomination, TURN over TCP/TLS, and
production UDP-buffer sizing remain future public-path work.

The committed native integration starts an independent reference-service
process and runs two production runner instances through registration,
authentication, bidirectional help with transport receipts, bidirectional
loopback-proxy payloads, reset, and close. Those runners live inside the Go
test process and use a controlled UDP Jamulus seam; this is not packaged-
binary, real-Jamulus, user-interface, secure-media, public-network, or acoustic
evidence.

The command accepts no flags or environment configuration. It reads one
strict JSON object per stdin line and writes allowlisted JSON events to stdout.
Current IPC version: `1`. Current secure session wire version: `3`.

In a frozen desktop build, the owner ignores environment path/build-ID
overrides and requires the packaged executable plus its canonical
`webjam-fabric.sha256` manifest. macOS seals the manifest as bundle data under
`Contents/Resources`; Windows and Linux keep it beside the executable in the
flat PyInstaller directory. WebJam verifies SHA-256, the native Mach-O, PE, or
ELF architecture, safe owner/mode where the platform exposes it, and the
sidecar's embedded build ID before accepting the process. macOS and Windows
also require their native platform signature. The ad-hoc macOS test signature
detects damaged or incompletely staged bundles; it is not a trusted publisher
identity and does not replace Developer ID signing/notarization. Linux has no
equivalent publisher-signature gate in this ZIP format; archive checksums and
the packaged manifest provide its integrity boundary.
An unsigned Windows artifact likewise fails the v3 native-signature gate by
design; legacy v1/v2 behavior remains available.

Build and verify with Go 1.25.13:

```sh
make check
make build-all VERSION=<source-build-id>
```
