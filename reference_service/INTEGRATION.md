# Native sidecar integration contract and proof boundary

The `reference-local` profile uses this service's native control and exact-pair
UDP protocols on fixed loopback endpoints. This service is not TURN and must
not be presented to Pion as one. Keep the separate Pion direct/TURN virtual-
network evidence separate from the native reference-service proof.

## Native client contract

The native client is implemented under `transport/internal/reference`; it belongs
inside the Go transport boundary, not in the desktop process. It must:

1. Derive the service session ID exactly as the existing IPC code does:
   `SHA-256("webjam/v3/session-id\x00" || 16-byte session_reference ||
   16-byte invite_reference)`. Reset preserves the first reference and rotates
   the second, so the fresh service ID does not hit the old ID's replay
   tombstone.
2. Both sides derive the one-use 32-byte enrollment token with HKDF-SHA-256:
   IKM is the raw 32-byte invitation capability, salt is the derived 32-byte
   session ID, and info is the exact ASCII string
   `webjam/v3/reference-service/enrollment-token`. The raw capability never leaves
   the native sidecar.
3. Host generates a private random 32-byte role token and registers the session
   ID, host role token, derived enrollment token, generation, and TTL over
   bounded NDJSON on service TCP 47131. `reference-local` relies on loopback;
   any external profile requires TLS 1.3. Successful registration means
   `host_waiting`; it does not mean that a peer is connected or audio is ready.
4. Guest derives the same enrollment token, generates a distinct private random
   32-byte role token, and enrolls exactly once. The service consumes the
   enrollment value at this operation, before bootstrap or peer proof. A bearer
   holder can therefore burn the invitation by enrolling and abandoning the
   proof; this does not open the data plane, and the host must Reset Invite.
   Neither role token nor the derived enrollment token belongs in the
   invitation, desktop settings, command line, environment, logs, or IPC events.
5. Both roles use `signal`/`poll` to exchange the bounded AEAD-sealed guest
   bootstrap and host acknowledgment, followed after QUIC TLS by bounded
   capability-authenticated, exporter-bound proofs. The service forwards the
   opaque fields without interpreting them. Pion's separate `WJSE`
   candidate-envelope path remains virtual-network evidence, not this native
   relay protocol.

For a two-process loopback proof, use the reference defaults. A public proof
needs a separately reviewed compiled endpoint profile and trusted TLS
validation; `reference-local` is loopback-only and cannot be reconfigured from
desktop IPC.

## Guest SPKI bootstrap before mutual TLS

The host pin is already in the invitation. The guest pin is not, so the current
candidate bundle cannot honestly claim both pins are known on its first message.
Use one bounded, capability-sealed guest-enrollment envelope before peer
exchange. A minimal canonical payload contains:

- magic/version, 32-byte session ID, generation, expiry, and a 16-byte nonce;
- the invitation-pinned 32-byte host SPKI hash;
- guest ephemeral certificate DER plus its computed 32-byte SPKI hash;
- sender role `guest` and no names, addresses, paths, or device data.

Seal it with a distinct HKDF/AEAD domain under the invitation capability and
service session ID. The host opens it using facts it already knows, checks
expiry/replay, parses the bounded self-signed Ed25519 certificate, recomputes
its SPKI hash, and only then records the guest pin. The host returns a separate
AEAD-sealed acknowledgment through reference control, binding the service
session, generation, both pins, the guest nonce, and a fresh host nonce.

QUIC mutual TLS still remains application-plane quarantined until each side
exchanges a capability-authenticated proof through reference control and
verifies the TLS exporter, service session/generation, both SPKI hashes, expiry,
role, and fresh nonce. The reference service never parses the AEAD-sealed
certificate/bootstrap or acknowledgment and does not interpret the proof
payload. This path uses no QUIC enrollment stream.

## Exact-pair relay as a QUIC PacketConn

The smallest relay proof bypasses Pion/TURN only for the relay leg:

- a wrapper owns one UDP socket pointed at service UDP 47132;
- `Bind` authenticates the observed endpoint for one role/session/generation;
- `WriteTo` rejects every address except a single synthetic peer and wraps QUIC
  ciphertext as DATA with a monotonic sequence and role MAC;
- `ReadFrom` accepts only DELIVERY from the configured service endpoint, verifies
  its receiving-role MAC/session/generation/replay window, strips the wrapper, and
  returns the same synthetic peer;
- writes over 1,350 bytes fail closed. Configure quic-go's UDP payload/Initial size
  accordingly; run an MTU test because this wrapper is not yet a low-MTU fallback.

Pass that constrained `net.PacketConn` to quic-go, run the existing mutual-TLS and
exporter-bound enrollment proof, then exchange a bounded live datagram and reliable
frame in both directions. Also prove wrong session, wrong role key, replay, changed
source endpoint, oversize write, and a third UDP socket receive nothing.

Opening the relay socket proves only an authenticated endpoint bind. The transport
must keep datagrams and streams quarantined until mutual TLS and the exporter-bound
proof complete. Only then may it emit `peer_connected`. There is no proxy-only
success event; `host_registered` means only that the one-use invitation is
registered and waiting for its guest.

That constitutes a real sidecar-to-reference relay proof only when the named
exchange is exercised between independent processes. It is not evidence that
the desktop reaches an Internet service, that Pion interoperates with this
wrapper, or that production TURN authorization exists.
