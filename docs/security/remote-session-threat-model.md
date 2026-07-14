# Remote session threat model

- Status: pre-release security contract
- Applies to: WebJam v0.12.0 private candidate's v3 remote-session design, one
  host plus one guest. The profile remains loopback-only and is not a deployed
  public service.
- Architecture: [ADR 0001](../adr/0001-remote-session-transport.md)
- Last reviewed: 2026-07-13

This document defines the security and privacy boundary for the v3 remote
session. It is an implementation and release gate, not evidence that a public
service or physical Internet rehearsal has occurred.

## Release decision

The static Go sidecar, authenticated path setup, exact-pair relay, and mutually
pinned QUIC TLS design is acceptable only when all three conditions below are
proven.

1. **Authenticate signaling before ICE.** Candidate bundles are canonical and
   authenticated before any candidate is given to Pion. QUIC authentication
   happens too late to protect initial ICE signaling by itself. An unsigned,
   modified, stale, replayed, cross-session, cross-role, or excess candidate
   must cause no network attempt.
2. **Restrict every relay path to its exact peer.** The native reference relay
   has no destination field and forwards only between the registered host and
   guest endpoints for the exact opaque session and generation. Its
   authenticated wrapper enforces replay, size, rate, byte, idle, and lifetime
   budgets and fails closed. Stock TURN authorization alone is insufficient;
   any future TURN profile requires a separate exact-pair policy proof.
3. **Keep v3 secrets out of process arguments.** A v3 URL is never accepted
   from `sys.argv` and no invite, capability, credential, address, musician
   name, or private path enters a sidecar command line. macOS may use the Qt
   file-open event. All platforms support paste. Windows remains paste-only
   until a click activation channel proves that the URL never enters a process
   command line.

No v3 failure may fall back to plaintext v2 Internet transport or expose the
Jamulus server publicly.

## Security objectives

WebJam protects the confidentiality, integrity, availability, and truthful
state of:

- live Jamulus audio and its participant routing;
- recording control, local originals, hashes, PCM facts, take attachment, and
  timeline gaps;
- the one-use invitation capability and all derived credentials;
- ephemeral certificate keys and peer pins;
- canonical participant identity and transport-to-participant bindings;
- host and service CPU, memory, disk, sockets, file descriptors, and bandwidth;
- musician names, installation identifiers, raw addresses, notes, recording
  names, Webex links, and home paths;
- readiness, path selection, reconnect, cleanup, and media-completion truth;
- the bundled sidecar and dependency/build provenance.

The relay and rendezvous are zero-trust for content and protocol input. They
may observe metadata and may drop, delay, replay, reorder, or fabricate input,
but must not be able to decrypt audio/media, enroll a peer, replace a peer key,
or alter an accepted candidate bundle.

The endpoint application, signed sidecar, bundled Jamulus, and the user's OS
account form the trusted endpoint computing base. Administrator/root access,
same-user malware, compromised audio drivers, and endpoint memory inspection
are residual endpoint risks.

## Assets and actors

| Asset | Primary harm if compromised |
| --- | --- |
| Unused invite capability | Unauthorized enrollment before the intended guest |
| Certificate seeds and traffic keys | Peer impersonation or endpoint plaintext disclosure |
| Live Jamulus packets | Private performance disclosure, injection, or interruption |
| Control state | False recording, presence, readiness, or reconnect state |
| Original WAVs and upload grants | Music disclosure, cross-take attachment, or disk abuse |
| Participant/take identity | Duplicate, confused, or misattributed musicians/media |
| Candidate/address metadata | Home-network privacy loss or SSRF targeting |
| Endpoint/service resources | Denial of service or paid-bandwidth abuse |
| Logs/support/crash data | Durable leakage beyond the intended session |
| Sidecar binary/dependencies | Compromise of every endpoint security property |

Relevant actors are the legitimate host and guest, an accidental or malicious
invite holder, a malicious enrolled guest, unauthenticated scanners, passive
and active network attackers, compromised rendezvous or relay operators,
untrusted local processes, same-user malware/administrators, support or crash
processors, and dependency/build-chain attackers.

The pilot authenticates possession of a private invitation, not a person's
real-world identity. WebJam cannot cryptographically distinguish the intended
drummer from a thief who obtains the unused link first.

## Trust boundaries

1. Clipboard, messaging app, custom URL, or paste field to the strict v3
   parser.
2. Python coordinator to the owned Go sidecar over inherited private pipes.
3. Jamulus to a random loopback UDP proxy owned by the sidecar.
4. Sidecar to the endpoints fixed by a compiled profile. `reference-local`
   permits only loopback native control and exact-pair relay; a future public
   profile would separately define and prove DNS/TLS/ICE/TURN behavior.
5. One sidecar to the other over authenticated QUIC TLS 1.3.
6. QUIC media streams to Python's canonical transfer store over a separate,
   bounded local media pipe.
7. Transfer store to private recording storage and take manifests.
8. Runtime evidence to logs, Connection Details, metrics, support, clipboard
   diagnostics, and crash reporting.
9. The signed application bundle to the exact sidecar executable and pinned
   dependency graph.

## v3 invitation and key schedule

The only accepted shape is:

`webjam://join?v=3&r=<profile>&i=<opaque-envelope>`

`r` is an application-shipped profile identifier, never a URL. The profile
maps to fixed transport/service configuration. `reference-local` fixes native
control and exact-pair relay endpoints to loopback and is lab-only. A future
public profile must reject redirects and non-public service resolutions and
separately define any STUN/TURN configuration it uses.

The fixed canonical envelope contains only a protocol marker, random logical-
session and invitation references, 256-bit one-use capability, expiry,
participant limit, and host ephemeral certificate fingerprint. The profile
remains the separate `r` field. Neither location contains a musician name,
address, port, path, long-lived relay credential, recording value, or
persistent account ID.

The host generates independent random values for:

- the logical session and invitation references used to derive the opaque
  service session ID;
- the one-use capability;
- the host service role/revocation token inside the sidecar;
- the host ephemeral Ed25519 identity.

HKDF-SHA256 derives independent versioned values from the capability using
distinct labels for reference-service enrollment, candidate signaling,
bootstrap, acknowledgment, and proof. The reference service receives the
session-bound derived enrollment token and opaque signaling payloads. It never
receives the raw capability or proof key, and one derived value cannot be
reused for another purpose.

The guest bootstrap and host acknowledgment are AEAD-confidential from the
service. Exporter proofs are authenticated but not encrypted: a compromised
service can inspect their random service-session ID, role, generation, expiry,
nonces, and public SPKI hashes, although it cannot forge a proof or recover the
capability. Those pseudonymous proof fields and signaling timing are explicit
metadata exposure.

Parsing is ASCII-only, length-bounded, duplicate-intolerant, strict about
unknown fields, and canonical: decoding and re-encoding must reproduce the
input. Mixed v2/v3 fields, percent-encoding ambiguity, Unicode confusables,
padding variants, fragments, userinfo, and copied surrounding log text fail.
An explicit v3 marker never falls through to legacy bare-address parsing.

## Enrollment and peer authentication

Candidate bundles bind the protocol, session locator, role, both available
certificate/SPKI pins, nonce, connection generation, expiry, and ordered
candidate list. Their canonical bytes are authenticated with the
candidate-signaling key before Pion sees them. Private candidates may also be
AEAD-wrapped so the rendezvous cannot inspect them; integrity authentication is
the minimum release gate.

For a production remote profile, WebJam accepts only bounded public-unicast
server-reflexive candidates and exact allowlisted relay candidates. Loopback,
private, link-local, multicast, broadcast, unspecified, documentation,
reserved, malformed, port-zero, and excess candidates fail before a packet is
sent. The existing same-LAN path remains a separate v1/v2 transport.

Reference enrollment and QUIC peer authentication proceed as follows:

1. TLS 1.3 and the exact `webjam/3` ALPN are required; QUIC 0-RTT is disabled.
2. The guest derives the session-bound reference enrollment token, supplies it
   with a fresh guest role token, and the service atomically consumes it at
   `Enroll`. A second enrollment is rejected before QUIC proof begins.
3. Through bounded reference-control `signal`/`poll`, the guest sends an
   AEAD-sealed bootstrap containing its bounded ephemeral certificate and pin;
   the host validates it and returns an AEAD-sealed acknowledgment bound to
   both pins and fresh nonces.
4. The guest verifies the host's exact invitation SPKI during QUIC TLS. The host
   requests one bounded valid ephemeral client certificate. Application
   datagrams and reliable streams remain gate-closed.
5. Each side creates a domain-separated proof over the TLS exporter, both SPKI
   hashes, service session, generation, expiry, role, and a fresh nonce. Proofs
   travel as bounded capability-authenticated reference-control payloads; the
   raw capability itself is never transmitted.
6. Each side verifies the other proof and exact TLS peer SPKI before authorizing
   its connection gate and starting peer pumps. Only then may it emit
   `peer_connected`.

There is no QUIC enrollment stream or reconnect credential in the current
reference path. “Mutually pinned” is true only after both proof verifications.
Before that transition, no application datagram or reliable stream API is
available. Reset closes the service session and requires a fresh invitation
and generation.

Because step 2 consumes the service enrollment value before steps 4–6, a thief
with an unused bearer can enroll and then abandon authentication. That cannot
open the data plane, but it burns the invitation and forces the host to use
Reset Invite. This account-free denial-of-service race is explicit residual
risk.

An application or sidecar restart ends the current reference session. The
current slice does not restore certificate/session seeds or reconnect state;
the host must create a fresh invitation and generation.

## Session data plane

Jamulus remains the only live-audio engine. In remote-host mode its server is
bound to `127.0.0.1`. The guest Jamulus client connects to a random loopback
proxy. The host sidecar uses one peer-specific loopback server-facing socket so
Jamulus retains distinct participant identity. No packet contains a proxy
destination, and neither peer can select an arbitrary local or remote target.

Each logical audio datagram carries, inside QUIC protection, protocol,
direction/channel, generation, monotonic sequence, and bounded payload length.
A sliding replay/deduplication window exists per direction and generation so
direct/relay overlap or a delayed old path cannot duplicate audio. Realtime
queues are bounded and prefer recent packets while counting every drop.

Control messages use a strict version/type/generation/request-ID/length frame.
Unknown types, invalid transitions, stale generations, duplicate forbidden
fields, and oversized frames fail closed. Transport identifiers map to the
existing participant, recording, take, Studio, and Logic models; the transport
does not create a second identity or recording truth.

Original media uses separate reliable streams and host-issued upload grants
binding session, participant, take, segment, SHA-256, declared size and PCM
facts, generation, and expiry. The peer never supplies a destination filename
or path. Exact duplicate chunks may be idempotent; changed or out-of-order
duplicates fail. Publication occurs only after size, SHA, and PCM validation,
and never overwrites a different existing final. Cancellation, revocation,
expiry, quota, and disk-full states preserve the guest original and keep the
take visibly incomplete.

Media streams are paced below live traffic and use a separate local pipe. A
slow disk or receiver applies QUIC flow control rather than growing an
unbounded queue or blocking audio/control.

## Threats, mitigations, and residuals

| Threat | Required mitigation | Residual risk |
| --- | --- | --- |
| Invite theft or race | 256-bit secret, short expiry, one service enrollment, atomic consume at `Enroll`, Reset Invite, visible roster, revocation | A thief can win enrollment or burn the invite without completing peer proof |
| Replay or cross-session confusion | Invitation-derived service session, role/transcript/generation binding, nonce cache, exact peer pin; fresh invite for reconnect | Endpoint compromise remains decisive |
| Downgrade | Separate parsers/transports, exact API/ALPN, no v3-to-v2 fallback | Old clients cannot join v3 |
| Rendezvous candidate injection | Authenticate canonical bundles before ICE; validate address class/count/size | Rendezvous can suppress or delay signaling |
| Rendezvous/relay MITM | Host pin from invite, guest key bound to proof, E2E QUIC | Metadata and availability remain exposed |
| Open relay, SSRF, port scan | Derived short credentials, no destination field, exact paired-address wrapper, fixed profiles, egress policy | The service can still deny service |
| QUIC handshake or parser flood | Stateless retry, pending limits, deadlines, token buckets, strict lengths, fuzzing | Distributed DDoS cannot be eliminated |
| Datagram replay, duplicate, reorder | QUIC protection plus app generation/sequence window | Loss and deliberate peer silence remain possible |
| Malicious enrolled audio | Per-peer socket, direction/size/rate checks, host mute/revoke | An enrolled guest can send objectionable audio |
| Forged or cross-take media | Host grant, per-peer authorization, generation, offset, SHA/PCM checks | Authorized media still consumes its quota |
| Path traversal or overwrite | UUID-derived names, private roots, no-follow/dirfd operations where available, conflict on existing final | Same-user filesystem tampering is residual |
| Disk/CPU/bandwidth exhaustion | Declared/session quotas, free-space reserve, concurrency/rate/byte limits, bounded hashing | Legitimate long recordings may hit the pilot limit |
| Backpressure starving audio | Dedicated media path, flow control, pacing, bounded queues, live priority | Transfers slow significantly on impaired links |
| Sidecar spoof, leak, or crash | Sibling-only manifest/hash/architecture/owner/signature/build-ID checks, inherited pipes, constant argv, bounded IPC, process-group/job ownership | Ad-hoc signing is not publisher identity; same-user compromise exposes endpoint plaintext |
| Log/support/crash leak | Secret-safe types, source avoidance, runtime sentinel redaction, allowlists, scrubbed/disabled crash reporting | Administrator memory dumps remain out of scope |
| Unsafe path switch while recording | Fresh generation, dedup, explicit gap and identity proof; otherwise defer | An audible gap may remain truthful but unavoidable |
| Clock skew | Service/host authoritative TTL and monotonic elapsed time; guest clock advisory | Broken system time can still break public TLS |
| Service restart or unreachable service | Authenticated re-registration or bounded degraded stop; TTL cleanup | Relay sessions drop until recovery |
| Dependency/build compromise | Pinned toolchain/modules/checksums, SBOM, license/vulnerability audit, signed bundle | Upstream zero-days remain possible |

## Relay and service abuse boundary

The rendezvous/relay keeps in-memory state by default. It stores no musician
accounts, names, audio, media, notes, or recording data. It uses opaque handles
and derived short-lived authorization, rejects arbitrary proxy destinations,
and removes state on authenticated end, revocation, idle expiry, or hard TTL.

The initial pilot constants must be explicit and tested. Adjustments require
updated measurements and threat-model review. Starting bounds are:

| Resource | Pilot bound |
| --- | ---: |
| Participants | one host plus one guest |
| Decoded invite / complete link | 768 bytes / 2,048 characters |
| Enrollment lifetime | 10 minutes |
| Reference session hard lifetime | 10 minutes |
| Host prepared-identity lifetime | 8 hours in sidecar memory; reset may reuse it, restart destroys it |
| Disconnected reconnect grace | None; reconnect requires Reset Invite |
| Relay idle expiry | 120 seconds |
| Concurrent bidirectional QUIC application streams | 4; unidirectional streams disabled |
| Enrolled guests per invitation | 1; a failed proof terminates that attempt and requires Reset Invite |
| Candidate bundle | 16 KiB and 32 candidates |
| Control frame / retained event timeline | 64 KiB / 256 entries |
| Realtime queue | 64 datagrams and 128 KiB |
| Control queue / media flow window | 1 MiB / 4 MiB |
| Media chunk / concurrent media streams | 256 KiB / 1 per peer |
| Segment / session media | 8 GiB / 20 GiB |
| Host free-space reserve | Future-v3 service policy: greater of 2 GiB or 10% |

The host-reserve row is a future-v3 service policy, not the current v1/v2
local-recording guard. The current source calculates a conservative PCM24
reserve from the session roster, with a 1-GiB minimum and 5-GiB warning floor.

The maximum Jamulus payload is fixed only after real 3.12.2 capture proves the
bound against negotiated QUIC DATAGRAM capacity. Packet/byte rates, burst
sizes, relay byte budgets, source registration limits, and global capacity are
configuration constants with deterministic overload tests; none may default
to unbounded.

The reference container runs non-root and read-only except bounded temporary
state, with no-new-privileges, explicit ports, and CPU/memory/file-descriptor
limits. Its public health response exposes only status and version. Normal
logs contain no capabilities, credentials, raw addresses, candidates, session
handles, or names.

## Secret and subprocess lifecycle

| Secret | Creation and use | Destruction/retention |
| --- | --- | --- |
| Invite capability | Host CSPRNG; transient host copy and guest paste; HKDF input | Service token consumed at `Enroll`; retained only as needed for current proof, then reset/close/expiry; never settings/disk/logs |
| Host management secret | Host CSPRNG; authenticated register/end/revoke | Session end or hard TTL |
| Certificate private keys | Endpoint memory; sidecar TLS identity | Guest close/process end; host reset may retain the prepared identity, shutdown destroys it |
| Candidate/enrollment keys | HKDF outputs in endpoint memory | Enrollment/session end as appropriate |
| Reference role/enrollment token | Independently derived, role/session scoped, short-lived | Session close or token TTL |
| Future TURN credential | Derived, peer/session scoped, short-lived | Allocation close or credential TTL |
| Reconnect credential | Not issued by `reference-local` | Reconnect requires a fresh invitation and generation |
| Media upload grant | Host-issued over QUIC; take/segment scoped | Complete, cancel, revoke, expiry, or end |

The sidecar is launched from one canonical bundle path without `PATH` lookup or
`shell=True`. Release startup verifies the expected build/protocol, file hash,
signature, architecture, ownership, and non-symlink path. argv is constant.
The environment is allowlisted and removes proxy/debug/TLS-keylog variables.
Secrets and private configuration enter through inherited pipes only. Unrelated
descriptors are close-on-exec.

Control and media use separate bounded pipes; product logs never copy a raw IPC
frame. stderr is discarded in production or drained through a fixed sanitized
ring during explicit diagnostics. Core dumps and TLS key logging are disabled.
POSIX uses an owned process group and Windows a kill-on-close Job Object. EOF,
unexpected exit, or protocol mismatch clears Ready state before bounded restart
or clean stop. App shutdown reaps the sidecar, Jamulus, pipes, sockets, relay
allocations, and session state.

Python and Go runtimes cannot promise perfect memory zeroization because of
immutable strings, copies, and garbage collection. The design therefore keeps
secret values out of durable and external surfaces by construction, minimizes
their lifetime, and treats endpoint memory inspection as residual risk.

## Privacy inventory

| Data | Visible to | Retention |
| --- | --- | --- |
| Full invitation/capability | Host and guest endpoint, intentional clipboard/messaging channel | Copy retires on authenticated `peer_connected`; bearer remains sensitive until reset/close/expiry; clear clipboard after TTL if unchanged |
| Host public key/fingerprint | Invitation, peers, rendezvous | Session TTL |
| Private/traffic keys | Endpoint parent/sidecar memory | Session lifetime |
| Reference control/relay credentials | Endpoint and service memory | Session/token TTL |
| Future TURN credentials | Endpoint and TURN service memory | Credential/allocation TTL |
| Public IPs/candidates | Peer, OS, STUN, rendezvous, TURN | Operational memory only; not normal logs |
| Timing, size, direction, byte counts | ISP and rendezvous/relay | Live accounting then aggregate/non-identifying evidence |
| Live audio | Jamulus and endpoint sidecars; ciphertext elsewhere | Never persisted by service |
| Original media | Guest storage and authorized host storage | User-controlled recording retention |
| Names, installation IDs, take IDs | Endpoints over E2E control | Local canonical product retention |
| Transport evidence | Endpoint bounded timeline | Coarse/numeric support facts only |
| Service session state | Opaque handles, expiry, generation, quotas | End/revoke/idle/hard TTL |
| Notes, Webex link, session/recording name | Endpoint product state only | Never rendezvous/relay/default support |

Unavoidable disclosures are explicit: a direct peer learns the other peer's
public address; service operators and ISPs see network metadata; the selected
clipboard/messaging channel sees the invitation; and endpoint malware or an
administrator can read endpoint content.

## Compatibility boundaries and remaining integration gates

The v0.10 support artifact is allowlist-first and the shared redaction filter
is the base for v3. The strict v3 types and ingress now close several legacy
exposure paths without changing v1/v2 compatibility:

- `RemoteInvitation` has constant secret-safe `repr`/`str`; its capability is
  never part of a dataclass representation;
- v3 explicitly rejects process-argument activation and uses Qt file-open or
  password-style paste ingress only;
- `SessionHud` retains only v3 invitation availability and requests transient
  serialization at the intentional clipboard boundary;
- recursive runtime redaction covers raw addresses and exact sentinels, and
  Sentry applies the same scrubber to events and breadcrumbs;
- frozen v3 startup ignores environment binary/build-ID overrides and validates
  the packaged sidecar's signed-bundle manifest SHA-256, expected architecture,
  safe owner/mode, native platform signature, and exact embedded build ID;
- v3 Jamulus host and guest launches omit `--clientname`; the musician name is
  applied only through authenticated loopback `jamulusclient/setName` after
  local-session proof;
- remote-host mode adds `--serverbindip 127.0.0.1`, while legacy LAN binding
  remains transport-aware;
- the v2 plaintext peer service and its mode-0600 absolute-path queues remain
  isolated to v2 and excluded from logs, crash reports, support, and v3 state.

The following are still release gates:

- the secure media authorization/quota model must be connected to the bounded
  reliable-stream runtime before v3 original transfer is claimed;
- final packaged execution of the binary checks, plus resource, accessibility,
  real Jamulus, and external/physical evidence, must match the required gates
  below.

## Required automated evidence

The release suite must prove at least:

- canonical v3 round-trip; opaque/identity-free envelope; secret-safe
  `repr`/`str`; duplicate, unknown, mixed-version, Unicode, percent, padding,
  length, copied-log, untrusted-profile, expiry, reset, consume, concurrent
  enrollment, restart, reconnect, cross-session, replay, and downgrade cases;
- Python/Go golden HKDF and canonical-signaling vectors; candidate mutation and
  replay rejection before outbound traffic; wrong host pin, wrong guest proof,
  TLS/ALPN mismatch, 0-RTT, exporter binding, generation, sequence, duplicate,
  direction, and malformed-frame rejection;
- relay ciphertext sentinel tests; exact paired-address pass; third-party IP,
  same-IP/wrong-port, old-generation, expired credential, quota, overload,
  idle, hard-TTL, revoke, end, and service-restart rejection/cleanup;
- sidecar argv/environment/process-list secrecy; exact binary/hash/signature
  selection; inherited-pipe-only secrets; IPC length/flood/deadlock behavior;
  crash/restart generation; process-group/job cleanup; repeated-session file
  descriptor, thread/goroutine, memory, and socket bounds;
- host-issued upload grants; per-peer/session/take/generation enforcement;
  traversal/symlink/overwrite rejection; exact duplicate handling; chunk,
  segment, session, disk-reserve, ENOSPC, cancel, revoke, expiry, SHA, PCM, and
  resume/attach-once behavior; no archive/decompression path;
- direct, forced relay, direct failure to relay, relay interruption, address
  change, loss/jitter/reorder/duplicate/blackhole, host/guest sidecar restart,
  upload interruption, recording interruption, end during upload, and quit
  during cleanup in the impairment lab;
- real Jamulus 3.12.2 decoded 440/660-Hz identity, roster distinction,
  reconnect, stems, local-original preservation, secure resumed transfer,
  Studio/Logic traversal, and complete owned-process/port cleanup for direct and
  forced-relay paths;
- privacy sentinels shaped like capability, TURN credential, private invite,
  IPv4, IPv6, username, home path, recording name, notes, Webex link, and key
  material absent from Python/Go logs, exceptions/tracebacks, Connection
  Details, metrics, support preview/copy/JSON/ZIP/logs, diagnostics clipboard,
  unauthorized manifest/filename fields, mocked crash events, process
  argv/environment/listing, container logs, and health output;
- `go test -race`, Go fuzz targets, pinned module verification, vulnerability
  and license/SBOM audits, container security/health gates, serial Python gates,
  packaged sidecar start/stop, and soak/resource thresholds.

The intentional invitation clipboard is the sole authorized clipboard surface;
diagnostics copy is always secret-free. Local take manifests may contain the
musician-approved recording identity required by the product, but transport
credentials, invitations, relay values, and raw addresses never do.

### Current native reference evidence

The native reference/IPC integration packages pass against an independently
spawned service process. They exercise one-use derived enrollment consumed
before proof, AEAD-sealed bootstrap/acknowledgment, exact peer pins, mutual TLS,
bidirectional exporter-bound authorization over opaque reference signaling,
application-plane quarantine until both proofs, real exact-pair UDP relay
traffic, loopback proxy payloads, reset, and bounded close. The two
production runners execute inside the Go test process and use a controlled UDP
endpoint instead of real Jamulus. Packaged binaries, real Jamulus routing,
secure original-media streams, public networks, and physical/acoustic evidence
remain open.

## Manual and external proof boundary

| Evidence | Release status until actually performed |
| --- | --- |
| Local/CI direct and forced-relay reference service | Required automated pass |
| Packaged sidecar on supported architectures | Required packaged pass |
| Public rendezvous/relay deployment | **NOT RUN by design** |
| Two independent homes and residential NATs | **NOT RUN** |
| Two-musician acoustic audibility | **NOT RUN** |
| Physical macOS/Windows interface route and headphones | **NOT RUN** |
| Guest original through a real Internet outage | **NOT RUN** |
| Logic Pro import and heard playback | **NOT RUN** |
| Packaged VoiceOver and NVDA review | **NOT RUN** |

Simulation, loopback, containers, virtual networks, decoded PCM, meters,
process state, RPC, and packet movement do not satisfy any physical or acoustic
item. A real external pilot additionally requires stable DNS, publicly trusted
TLS, reachable native relay UDP or separately proven STUN/TURN infrastructure,
firewall/egress policy, monitoring, capacity and abuse response, credential
rotation, incident handling, and a deployment-specific privacy review.

## Acceptance and residual-risk rule

The account-free one-use invite race, endpoint compromise, network metadata,
traffic analysis, distributed denial of service, truthful interruption gaps,
runtime zeroization limits, and upstream zero-days are accepted residual risks
for this private pilot only after the mitigations above pass.

Any failure of pre-ICE authentication, exact relay pairing, v3 process-command
secrecy, certificate pinning, replay/generation isolation, media authorization,
resource bounds, privacy sentinels, or cleanup is a release blocker. WebJam
must stop with one useful action rather than weaken encryption, expose Jamulus,
reuse an invite, claim false readiness, or mark incomplete media complete.
