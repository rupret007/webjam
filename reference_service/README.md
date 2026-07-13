# WebJam v3 reference rendezvous and relay

This is the smallest self-hostable reference implementation of WebJam's v3
rendezvous boundary. It provides:

- bounded, versioned, one-host/one-guest registration;
- atomic one-use service enrollment with a ten-minute default lifetime,
  consumed before QUIC peer proof (so an unused bearer can be burned and must
  then be reset);
- role-authenticated queues for opaque end-to-end sealed candidate envelopes;
- an authenticated UDP relay that can forward only to the registered opposite
  peer in the same session and generation;
- replay, malformed-input, downgrade, rate, bandwidth, memory, connection, idle,
  and capacity controls;
- privacy-safe JSON health and aggregate diagnostics;
- no audio decoding, media persistence, arbitrary destination field, names, or
  invitation logging.

It uses only the Python 3.12 standard library and keeps all state in bounded
memory. Restarting the service intentionally ends every session.

## Run locally

The defaults bind all three listeners to loopback:

```sh
cd reference_service
python3.12 -m webjam_reference
```

Listeners are control TCP `127.0.0.1:47131`, exact-peer relay UDP
`127.0.0.1:47132`, and health HTTP `127.0.0.1:47133`. The control listener is
plaintext only because it is loopback. Never expose it that way.

```sh
python3.12 -m pytest -q
python3.12 -m ruff check .
```

See [PROTOCOL.md](PROTOCOL.md) for the exact frames and privacy contract, and
[INTEGRATION.md](INTEGRATION.md) for the smallest honest sidecar/QUIC proof.

## Container

The image is non-root, read-only compatible, dependency-free, pins its Python base
image, and has a healthcheck. Refresh that digest through normal image review and
scanning; its own runtime defaults remain loopback-only.

```sh
docker build -t webjam-reference:0.1.0 .
docker run --rm webjam-reference:0.1.0
```

For an externally reachable native-protocol test, provide a real certificate and
key and explicitly bind control and relay. `compose.example.yaml` shows the
required opt-ins and a restricted runtime. Do not put token values in environment
variables, command arguments, image layers, or compose files. Certificate files
mounted into the example must be readable by the image's unprivileged UID/GID
10001 without making the private key broadly writable.

## Desktop integration boundary

The compiled desktop profile named `reference-local` is deliberately lab-only
and fixes native control to `127.0.0.1:47131` and the native exact-pair relay to
`127.0.0.1:47132`. Desktop IPC selects only that profile ID; it cannot provide
or override endpoints. The service intentionally does **not** impersonate HTTP,
WebSocket, STUN, or TURN:

- reference control is TLS-capable TCP NDJSON on port 47131;
- relay traffic uses the authenticated exact-pair wrapper on UDP 47132;
- session IDs are sidecar-derived 32-byte SHA-256 values;
- signaling envelopes are forwarded byte-for-byte as sealed payloads.

The native sidecar contract is documented in [INTEGRATION.md](INTEGRATION.md).
Pion ICE/TURN direct and relay behavior is tested separately in a deterministic
virtual network; those tests are not evidence that this service is TURN or
that a public Pion/TURN deployment exists. Any future stock-TURN path still
needs an authorization and packet-policy layer that enforces the same exact
session, generation, role, peer, size, rate, and lifetime constraints.

There is no compiled public profile. `reference-local` must never be changed
into a user-configurable endpoint or used as evidence of Internet reachability.

## External infrastructure required

An actual Internet deployment needs all of the following outside this process:

- public DNS A/AAAA records and a trusted TLS 1.3 certificate;
- firewall rules for control TCP 47131 and relay UDP 47132 (or mapped ports), with
  health HTTP left private;
- a UDP-capable load balancer or direct host address whose flow idle timeout is
  longer than the configured keepalive/90-second session idle bound;
- session-affine routing. State is deliberately local and is not shared between
  replicas; failover ends the affected session instead of copying secrets;
- upstream volumetric DDoS protection and connection-rate limiting;
- capacity alerts based on `/healthz` and private `/diagnostics` aggregate data;
- certificate rotation, image scanning/signing, OS patching, and secret delivery
  outside container arguments/environment;
- a separately reviewed compiled public profile, plus real
  dual-stack/NAT/MTU/impairment and geographic latency validation before
  production use.

The service does not provide DNS, certificates, a TURN-compatible listener, load
balancing, durable sessions, cross-replica migration, or Internet availability by
itself.
