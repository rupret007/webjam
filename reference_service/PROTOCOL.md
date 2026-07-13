# WebJam reference service protocol

This document specifies reference-service protocol version 3. It is deliberately
small. The service knows an opaque 32-byte session identifier, two roles, bounded
ciphertext, and exact observed UDP endpoints. It never receives a display name,
Jamulus name, session title, media filename, arbitrary relay destination, or full
invitation URL.

## Control transport

Control uses newline-delimited canonical JSON over TCP. Internet deployments MUST
use TLS 1.3, either directly with `WEBJAM_TLS_CERT` and `WEBJAM_TLS_KEY` or through
a trusted local TLS sidecar. Each input line is at most 16,384 bytes. Every request
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

`generation` defaults to 1. TTL is 30–600 seconds by default. The response fixes
`participant_limit` at 1. Duplicate live IDs conflict; recently closed/expired IDs
are tombstoned and rejected as replays.

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

Responses are `{"v":3,"ok":true,...}` or
`{"v":3,"ok":false,"error":"<bounded-code>"}`. Public errors are categorical:
`malformed`, `frame_too_large`, `unsupported_version`, `unknown_operation`,
`invalid_ttl`, `session_conflict`, `session_replayed`, `invalid_enrollment`,
`enrollment_used`, `unauthorized`, `replay`, `queue_full`, `rate_limited`, and
`overloaded`.

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
