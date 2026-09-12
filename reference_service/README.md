# WebJam v3 reference rendezvous and relay

This is the smallest self-hostable reference implementation of WebJam's v3
rendezvous boundary. It provides:

- bounded, versioned, one-host/one-guest registration;
- approved-host registration over verified client TLS when admission is enabled,
  while invited guests need no client certificate;
- atomic one-use service enrollment with a ten-minute default lifetime,
  consumed before QUIC peer proof (so an unused bearer can be burned and must
  then be reset);
- a separate eight-hour maximum enrolled-room lifetime from registration,
  with the existing 90-second idle timeout;
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

Bind settings accept unscoped numeric IPv4/IPv6 addresses or the exact name
`localhost` (case-insensitive). Other hostnames, zone identifiers and empty values
are rejected before creating resources; startup never starts a DNS lookup.
Control `localhost` binds IPv6 and IPv4 loopback to the same port, falling back
to IPv4 only when IPv6 loopback is unavailable. An explicit IPv6 bind remains
IPv6-only and fails if unavailable. HTTP and UDP `localhost` use `127.0.0.1`.
This intentionally narrows service bind configuration; it does not restrict the
DNS name used to verify the service's TLS certificate or add desktop endpoint
settings.

```sh
python3.12 -m pip install '.[test]'
python3.12 -m pytest -q
python3.12 -m ruff check .
```

The test extra includes `cryptography` solely to generate disposable certificates
for real loopback TLS tests. The service runtime remains standard-library-only.

See [PROTOCOL.md](PROTOCOL.md) for the exact frames and privacy contract, and
[INTEGRATION.md](INTEGRATION.md) for the smallest honest sidecar/QUIC proof.

## Enrollment and room duration

The default 600-second invitation TTL limits when a guest may enroll. Unenrolled
rooms still expire at that deadline even if the host is active. After successful
service enrollment, authenticated activity can keep the room alive beyond the
invitation deadline, until its independent hard limit of eight hours from the
original registration. Enrollment and traffic never reset that hard limit.
The 90-second idle timeout, authenticated host close, and service shutdown can
end it earlier.

`--max-active-session-seconds` or `WEBJAM_MAX_ACTIVE_SESSION_SECONDS` can lower
the room limit. It must be an integer at least as large as the configured maximum
enrollment TTL and no greater than 28,800 seconds. The existing
`--max-session-ttl-seconds` / `WEBJAM_MAX_SESSION_TTL_SECONDS` still configure
enrollment admission; the registration and enrollment wire responses still
report admission TTL, not active-room duration.

Service enrollment does not prove native mutual peer authentication. A bearer
holder can still enroll and abandon the later proof. Both that room and a room
whose remaining role keeps sending valid traffic remain bounded by the hard
limit, idle timeout, capacity/rate limits, and authenticated host revocation.
Native peers must enforce their own admission, identity, active-lifetime, and
cleanup bounds. These service tests do not establish physical session duration
or Internet reachability.

## Container

The image is non-root, read-only compatible, dependency-free, pins its Python base
image, and has a healthcheck. Refresh that digest through normal image review and
scanning; its own runtime defaults remain loopback-only.

```sh
docker build -t webjam-reference:0.1.0 .
docker run --rm webjam-reference:0.1.0
```

Exposing either control or relay requires a server certificate/key, a dedicated
host client CA, and an approved-host manifest. The legacy insecure-control flag
cannot bypass these checks. `compose.example.yaml` shows the required mounts and
a restricted runtime; it is not deployment approval or proof of Internet safety.
Do not put token values in environment
variables, command arguments, image layers, or compose files. Certificate files
mounted into the example must be readable by the image's unprivileged UID/GID
10001 without making the private key broadly writable.

## Host admission

Configure `--host-client-ca` / `WEBJAM_HOST_CLIENT_CA` and
`--host-admission-file` / `WEBJAM_HOST_ADMISSION_FILE` together with built-in TLS.
The dedicated CA verifies client certificate chains. The manifest additionally
approves exact leaf certificates; a CA-signed certificate alone cannot allocate
a room. Its strict JSON shape is:

```json
{"v":1,"hosts":[{"principal":"<32 lowercase hex characters>","certificate_sha256":"<64 lowercase hex characters>"}]}
```

Principals are opaque operator-assigned IDs, not participant display names.
The fingerprint is SHA-256 of the leaf's DER encoding. The regular file must be
nonempty, at most 65,536 bytes, and contain 1–128 unique principals and unique
fingerprints with no additional fields. Symbolic links are rejected. Loading
requires two bounded reads through the same descriptor to agree, with file
identity and metadata checks before and after. Detected content or metadata
changes are refused; this finite check is not an atomic filesystem snapshot.
Treat the file and its containing directory as trusted operator configuration.
No client keys belong in it.

Registration rechecks certificate validity against wall-clock time on every
request, including on a connection opened before expiry. Each approved host can
allocate at most four waiting or enrolled rooms by default, also subject to the
global capacity. `--max-sessions-per-host` / `WEBJAM_MAX_SESSIONS_PER_HOST` accepts
1–256. A fixed per-host bucket permits one attempt per second with a burst of
four, before the global registration bucket. Rejected or unknown host identities
cannot create quota entries. Close, expiry and shutdown release room counts.

Invited guests and fresh role-token-authenticated Close connections omit client
certificates. An invalid certificate that a client does present fails TLS before
any operation, so clients must not attach expired host credentials to these
connections. A valid certificate never substitutes for a room's role token.

Policy replacement or revocation requires a deliberate restart, which ends all
rooms and clears their secrets. There is no hot reload or online issuer.
Certificate expiry removes new registration authority; it does not itself end
an existing room. Room idle and lifetime limits still apply.

This service-side boundary does not provision ordinary hosts. Desktop credential
issuance, protected storage, renewal/recovery and an approved Internet profile
remain unfinished; guests must not inherit certificate setup requirements.

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
- reviewed host credential provisioning and an authenticated UDP return-path
  proof before treating an observed source address as reachable;
- a separately reviewed compiled public profile, plus real
  dual-stack/NAT/MTU/impairment and geographic latency validation before
  production use.

The service does not provide DNS, certificates, a TURN-compatible listener, load
balancing, durable sessions, cross-replica migration, or Internet availability by
itself.

## Connection setup and shutdown

Control setup has its own listener capacity and monotonic rate bucket before
TLS allocation. Defaults are 64 pending setups, 32 setup starts per second and a
burst of 64. The corresponding flags are `--max-pending-handshakes` (1–512),
`--control-accepts-per-second` (1–1024) and `--control-accept-burst` (1–1024);
environment names are `WEBJAM_MAX_PENDING_HANDSHAKES`,
`WEBJAM_CONTROL_ACCEPTS_PER_SECOND` and `WEBJAM_CONTROL_ACCEPT_BURST`.
These limits also apply to plain loopback lab setup. The separate completed
control cap remains 512; HTTP's active cap remains 64. Successful TLS still needs
an available completed-connection slot before application dispatch.
HTTP has its own setup capacity and bucket using these same settings; health
traffic cannot consume the control bucket. Each listener uses one 10 ms polling
timer with at most 16 nonblocking accept attempts per callback, rotating fairly
across address families. Event-loop load can delay a callback. This polling is
only for control/health connection setup; it is outside the media and UDP paths.
Documented transient peer-accept errors retain that bounded polling and allow
later guests to connect. Invalid listener state, resource exhaustion and unknown
errors still fail closed; the pending-network error list is platform-specific.

Transport-capacity refusal closes the connection without a TLS, control JSON or
HTTP response. Requests already admitted to a handler retain their protocol error
responses. Nonblocking acceptance uses an owned, bounded polling callback so each
raw socket is tracked before any asynchronous setup. These are application
resource bounds; an upstream flood can still
saturate kernel queues or the network. They do not replace upstream protection.

Owned response writes and connection retirement have three-second default
deadlines. Startup and teardown belong to a single-use service instance. Close
immediately refuses new work, cancels and joins startup, retires late-created
listeners, wipes room state and aborts tracked peers before joining tasks. The
five-second TLS handshake deadline contributes to one overall eight-second
default shutdown budget; the budget does not reset per peer or cleanup stage.
Cancelling a caller awaiting Close does not cancel teardown. A failed join reports
failure and retains cleanup ownership; it never reports success by dropping task
records. These bounds do not establish Internet availability or complete abuse
resistance.
