# WebJam reference service protocol

This document specifies reference-service protocol version 3. It is deliberately
small. The service knows an opaque 32-byte session identifier, two roles, bounded
ciphertext, and exact observed UDP endpoints. It never receives a display name,
Jamulus name, session title, media filename, arbitrary relay destination, or full
invitation URL.

## Control transport

Control uses newline-delimited canonical JSON over TCP. Exposing either control
or relay requires built-in TLS 1.3 (`WEBJAM_TLS_CERT`, `WEBJAM_TLS_KEY`) and host
admission (`WEBJAM_HOST_CLIENT_CA`, `WEBJAM_HOST_ADMISSION_FILE`). The legacy
insecure-control flag cannot authorize exposure through a TLS sidecar.
Each input line is at most 16,384 bytes. Every request
contains `"v":3` and an operation name; unknown and extra fields are rejected.
Binary values are unpadded canonical base64url.

The 32-byte service session ID is the sidecar's domain-separated SHA-256
derivation from the invitation's 16-byte logical session reference followed by
its 16-byte invitation reference. Reset Invite preserves the logical session
reference but rotates the invitation reference, producing a new service ID that
does not collide with the closed ID's replay tombstone. `host_token` and
`guest_token` are independent random 32-byte values. `enrollment_token` is
instead derived with HKDF-SHA-256 using the raw 32-byte invitation capability as
IKM, the derived 32-byte service session ID as salt, and the exact ASCII info string
`webjam/v3/reference-service/enrollment-token`, with a 32-byte output. The raw
invitation capability MUST remain inside the native sidecar and MUST never cross
the reference-service connection. Neither the capability nor the derived
enrollment token may be reused as a role token. Service credentials transit only
over loopback-local control or TLS 1.3 and are held only in bounded process
memory. The registry retains token hashes plus derived UDP MAC keys and wipes the
derived keys when a session ends.

### Register

```json
{"v":3,"op":"register","session":"<32B>","host_token":"<32B>","enrollment_token":"<32B>","generation":1,"ttl_seconds":600}
```

`generation` defaults to 1. Enrollment admission TTL is 30–600 seconds by default.
The response echoes the accepted admission `ttl_seconds` and fixes
`participant_limit` at 1. Duplicate live IDs conflict; recently closed/expired IDs
are tombstoned and rejected as replays.

When host admission is enabled, registration additionally requires a currently
valid client leaf certificate verified by the dedicated host CA and explicitly
listed in the startup manifest. The service derives the principal from the
completed TLS connection, never a control field, certificate name or display
name. Missing or unapproved identity yields `unauthorized` before registry
allocation or quota mutation. Invalid presented certificates fail TLS itself.
Validity is checked against wall-clock time on every registration; existing room
lifetime and rate buckets use a monotonic clock.

The immutable manifest approves 1–128 unique opaque principals and leaf SHA-256
fingerprints. Replacing it takes effect only at restart, which wipes all rooms.
Certificate expiry denies new registration but does not confer or revoke room
role authority. Per-principal allocation attempts are limited to one per second,
burst four, before the global bucket. Waiting and enrolled rooms both count
toward the default four-room host cap and global capacity; removal releases the
count exactly once. No host identity or fingerprint appears in diagnostics.
All-loopback lab operation without admission retains its existing behavior.

### Enroll once

```json
{"v":3,"op":"enroll","session":"<32B>","enrollment_token":"<32B>","guest_token":"<32B>"}
```

The enrollment value is consumed atomically when this operation succeeds. A
second guest, including a replay by the first guest, receives `enrollment_used`.
Unknown sessions and wrong values use the same `invalid_enrollment` error.
Consumption happens before guest bootstrap, QUIC TLS, exporter-proof exchange,
or `peer_connected`. A bearer holder can therefore enroll and abandon the later
proof, burning the invitation without opening the application data plane; the
host must use Reset Invite.

An invited guest does not need a client certificate. Neither an approved host
certificate nor a known principal replaces the enrollment capability.

The response's `ttl_seconds` is the whole seconds remaining until the original
admission deadline, including zero for a valid enrollment within the final
fractional second. It does not report or extend the enrolled-room lifetime.

### Room lifetime

An unenrolled room expires at its original admission deadline. A successfully
enrolled room instead has a hard active ceiling of eight hours from original
registration, configurable downward with `max_active_session_seconds` but never
above 28,800 seconds or below the configured maximum admission TTL. Enrollment
and subsequent activity cannot reset that ceiling. All rooms also retain the
90-second default idle timeout. Only accepted role-authenticated operations and
relay traffic refresh activity; rejected authentication/replay/endpoint traffic
cannot prolong idle lifetime.

The service observes enrollment, not the later native mutual peer proof. An
enrolled room remains subject to the finite ceiling even if the peer abandons
proof or a single role keeps sending valid activity. Native clients must enforce
their own proof deadline, certificate validity, active ceiling, and teardown.
On any service expiry or authenticated host close, queued signaling and relay
keys/endpoints are cleared, capacity is released, and the existing bounded
registration tombstone is retained. This adds no control operation or response
field and changes no listener or endpoint policy.

### Publish opaque authenticated signaling

```json
{"v":3,"op":"signal","session":"<32B>","role":"host","token":"<32B>","generation":1,"sequence":1,"sealed_payload":"<16..8192B>"}
```

The role token authenticates the publisher to the reference service over the
protected control transport (loopback for `reference-local`, TLS 1.3 for any
external deployment).
`sealed_payload` is the protocol field for an opaque native-sidecar payload. The
guest bootstrap and host acknowledgment are AEAD-sealed; exporter proofs are
capability-authenticated and TLS-exporter-bound. The service verifies only the
field's canonical encoding and bounds; it does not decrypt, deserialize, log, or
otherwise interpret it. Each role has a 64-entry replay window. A signal is
queued only for the opposite role.

### Poll and close

```json
{"v":3,"op":"poll","session":"<32B>","role":"guest","token":"<32B>","generation":1,"sequence":1}
{"v":3,"op":"close","session":"<32B>","role":"host","token":"<32B>","generation":1,"sequence":2}
```

Each poll consumes at most one sealed payload so the response stays below the
control-frame bound. Only the host may close a session. All authenticated control
operations share the role's replay window.

Close authority belongs to the host role token, session, and generation, not
the TCP connection that registered the room. A client whose original control
connection has reached the 30-second default read-idle timeout may use a fresh
connection with the same authenticated close operation and a valid subsequent
control sequence. A failed/uncertain response is not proof of remote revocation;
client retry and shutdown must remain bounded.

A fresh Close connection also needs no client certificate. Clients must omit
invalid or expired host certificates on guest/Close connections: TLS rejects an
invalid presented certificate even when the operation itself needs none.

Responses are `{"v":3,"ok":true,...}` or
`{"v":3,"ok":false,"error":"<bounded-code>"}`. Public errors are categorical:
`malformed`, `frame_too_large`, `unsupported_version`, `unknown_operation`,
`invalid_ttl`, `session_conflict`, `session_replayed`, `invalid_enrollment`,
`enrollment_used`, `unauthorized`, `replay`, `queue_full`, `rate_limited`, and
`overloaded`. Unexpected handler failures return categorical `internal_error`
without exception details.

Control and HTTP response writes and connection retirement each have finite
three-second default deadlines. TLS handshakes have a five-second deadline.
Shutdown rejects late handlers, stops listeners, erases registry state and
aborts tracked writers before joining listeners and tasks, using one overall
eight-second default budget including pending handshakes. A failed join reports
failure. The completed-control-connection cap does not bound simultaneous TLS
handshakes; this still needs a pre-exposure concurrency boundary.

## Exact-peer UDP relay

The relay is a WebJam wrapper, not a general TURN server. A client cannot put a
destination IP or port in a packet. After both roles bind, the only possible route
is `(opaque session, host endpoint) <-> (same session, guest endpoint)`.

Each packet has this network-byte-order layout:

| Field | Bytes | Rule |
| --- | ---: | --- |
| magic | 4 | `WJR3` |
| version, role, kind, flags | 4 | v3; host=0/guest=1; flags=0 |
| session | 32 | exact registered opaque ID |
| generation | 4 | exact registered generation |
| sequence | 8 | 63-bit, replay-window checked |
| payload length | 2 | exact remaining payload length |
| opaque payload | 0–1350 | never decoded or persisted |
| MAC | 16 | truncated HMAC-SHA-256 over preceding bytes |

The MAC key is `HMAC-SHA-256(role_token,
"webjam-reference-relay-v3")`. Client kinds are BIND=1, DATA=2, and KEEPALIVE=3.
The service emits DELIVERY=4, re-authenticated with the receiving role's key. A
role's first authenticated BIND fixes its exact observed IP/port for that session
generation; packets from any other endpoint are dropped. DATA is never reflected
to its sender and is never forwarded until the opposite role is enrolled and
bound.

An authenticated first BIND proves possession of a role key, not return-path
reachability of its source address. This protocol has no challenge/confirmation
exchange yet. Host admission alone does not close that gap: an admitted hostile
host could control both role keys. A reviewed return-path proof is required
before public exposure; exact-peer forwarding must not be described as such a
proof.

The default envelope is at most 1,420 bytes, per-session traffic is bounded by
datagram and byte token buckets, and duplicate/old/malformed/version-mismatched
packets are silently dropped. The relay sees ciphertext only; it does not implement
audio codecs, QUIC parsing, media storage, or arbitrary UDP proxying.

The 1,350-byte inner bound can carry a QUIC Initial constrained to 1,200 bytes;
the authenticated outer packet fits a common 1,500-byte IPv6 Ethernet path. It
does not prove lower-MTU Internet paths. A native client must cap QUIC UDP payloads
at 1,350 bytes, reject oversized writes, and treat PMTU failure as a relay-path
failure until fragmentation-free lower-MTU framing is implemented and tested.

## Health and diagnostics

`GET /healthz` returns only status and protocol version. It returns HTTP 503 when
session or global signaling capacity is exhausted. `GET /diagnostics` reports
aggregate counts and categorical drops. Neither endpoint includes tokens, opaque
session IDs, payloads, filenames, names, or peer addresses. Responses carry
`Cache-Control: no-store`.
