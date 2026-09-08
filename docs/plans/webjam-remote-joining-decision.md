# WebJam remote joining: decision prepared for Jeff

Prepared 2026-09-08 against common master `2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2`.
Carried into the declared #96–#100 integration candidate; those slices do not
change the networking implementation described here. No deployment, configuration, account,
certificate, firewall, signing, spending, or live meeting action was performed.

## Decision in plain language

The smallest route to ordinary first-time guests joining from different homes
with one WebJam invitation is to finish the existing v3 transport against a
self-operated, Internet-reachable service with private invitations and no room
directory. It would still be a publicly reachable rendezvous endpoint. Calling
its rooms private does not satisfy an absolute ban on public rendezvous.

Recommended next authorization: allow **design, implementation, and isolated
testing** of that specific v3 service path, with no public room discovery, short
codes, arbitrary destination proxying, deployment, or spending. Public exposure
would require a separate concrete deployment approval after review.

If the current prohibition must remain absolute, use a separately approved,
pre-enrolled private network for a limited two-person pilot. That can give the
prepared participants one WebJam invitation per session, but it adds VPN/client
onboarding before their first session. It does not prove a first-time guest can
join with WebJam alone. There is no currently implemented path that provides
both that zero-extra-setup experience and the absolute networking prohibition.

## What is implemented today

- Ordinary Art v2 invitations address RFC1918 IPv4 only. The control/media plane
  is plaintext HTTP with a bearer; `core/network_invite.py:96` deliberately
  rejects public IPs, hostnames, IPv6, and non-RFC1918 space. Keep that boundary.
- `local_band_address()` in the same file, line 302, deliberately prefers the
  Mac's physical Wi-Fi address over VPN routes. Installing a VPN does not make
  the existing Copy Invite action choose a remotely reachable VPN address.
- Both Python (`core/rendezvous_profiles.py:139`) and Go
  (`transport/internal/profile/profile.go:15`) compile only `reference-local`.
  Its TCP 47131 and UDP 47132 destinations are loopback.
- The production native orchestrator explicitly rejects other profile IDs
  (`transport/internal/ipc/reference_orchestrator.go:56`), calls `DialLocal()`
  for plaintext loopback TCP, and calls `OpenRelayLocal()` for a loopback UDP
  socket and destination. The relay constructor itself rejects a non-loopback
  destination (`transport/internal/reference/relay.go:90`). Changing only a
  Python profile or service listener cannot enable remote sessions.
- The separate Python reference service already supports TLS 1.3 listeners,
  bounded in-memory sessions, single-use enrollment, sealed signaling, exact
  host/guest UDP forwarding, expiry/revocation, rate limits, and private aggregate
  health. See `reference_service/README.md`, `PROTOCOL.md`, and
  `webjam_reference/server.py:421`.
- Pion ICE direct/STUN/TURN tests are a separate lab path. The native reference
  relay is not TURN. Existing ICE candidate policy rejects private addresses and
  CGNAT explicitly (`transport/internal/icequic/agent.go:280`). Do not relax that
  global policy merely to route an overlay experiment.

## Option comparison

| | A. Self-operated Internet endpoint; private invitations | B. Private service behind pre-enrolled VPN |
|---|---|---|
| Guest's first use | Install approved WebJam build, receive complete invitation, Join; external Conversation still an explicit handoff | Install/configure VPN, authenticate/enroll device, obtain approved network access, then install WebJam and Join |
| Every later session | One WebJam invitation | One WebJam invitation while VPN remains connected and authorized |
| Reachability | Known service DNS, trusted TLS control, reachable UDP relay; neither home needs inbound port forwarding to WebJam | Both devices need a working route to the private service; VPN deployment/onboarding is a prerequisite |
| Public rendezvous hold | Requires explicit narrow exception even with no public rooms | WebJam service can remain private; approval still needed for the VPN's own endpoints and any vendor coordination/relay |
| Transport work | Non-lab compiled profile, TLS native client, non-loopback fixed relay endpoint, admission and lifetime work | The same endpoint/profile/lifetime work, plus overlay binding and MTU compatibility; not a settings-only workaround |
| Security admission | New host-admission control; guests retain one-use invitation capability | VPN access policy limits service users; existing per-session authorization still required |
| Music suitability | Relay geography and actual path must be measured; direct native path remains a later separate project | VPN can itself relay before WebJam relays; actual topology/latency must be measured |
| Claim possible after successful pilot | Evidence for the exact tested separate-home setup and platforms | Evidence for the pre-enrolled private-network setup only |

## Concrete code and profile work before either pilot

1. Add a separately named, immutable deployment profile in Python and Go after
   an actual endpoint owner is identified. Keep `reference-local` loopback-only.
   The profile must provide exact control DNS/port, TLS server identity/trust,
   relay destination/port, address-family policy, and bounded packet policy.
   Invites and IPC continue to carry only the allowlisted profile ID; never an
   arbitrary URL, host address, proxy destination, or certificate override.
2. Extend native control dialing to TLS 1.3 with verified certificate chain and
   expected DNS name. Do not use `InsecureSkipVerify`, ambient endpoint overrides,
   or the service's insecure-public-control escape hatch. Certificate renewal,
   wrong-name, expired/untrusted certificate, timeout, and cancellation need
   isolated tests. The service supports TLS already; the native `DialLocal()`
   client does not.
3. Extend the native exact-pair relay through a profile-specific constructor.
   Choose a routable outbound socket instead of loopback, permit only the
   provisioned relay endpoint, and preserve exact-source, role, generation,
   HMAC/replay, size/rate, and opposite-peer enforcement. Keep all application
   Jamulus sockets loopback-only. This is an outbound encrypted v3 path, not
   public Jamulus or public v2 HTTP. DNS resolution must not open arbitrary
   relay destinations; pin/allowlist the deployment's actual resolved relay
   addresses for the session and define controlled address rotation.
4. Extend the native orchestrator's profile gate deliberately, preserving the
   present quarantined peer-authentication sequence: guest pins host SPKI from
   the invitation; guest key bootstrap is sealed; both sides prove capability
   ownership bound to TLS exporter, peer identities, and exact generation before
   application packets are accepted. Raw invitation capability never reaches the
   service; only its domain-separated enrollment token does.
5. Select the authorized profile through host product configuration/build
   policy. Today's native host path depends on `WEBJAM_ENABLE_REFERENCE_LOCAL=1`
   and defaults `NativeHostTransportOwner` to `reference-local`; that developer
   flag is not an acceptable guest setup instruction or production feature flag.
   Host/Join must remain simple and unavailable service/profile must fail clearly.

## Three additional prerequisites that endpoint provisioning does not solve

**Host admission.** Existing service Register accepts a newly chosen session ID
and host/enrollment tokens supplied by the requester; it has no provisioned host
account/device admission credential. TLS encrypts that request but does not make
registration private. Before option A exposure, require separately reviewed host
admission, for example an enrolled host-device mTLS identity or equivalent
short-lived signed authorization. Do not embed a shared admin token in WebJam.
Guests should continue to use their one-use invite, without a second service
account. For option B, explicit VPN grants can provide network admission to a
small pilot cohort; that is not authorization to weaken the session protocol.

**Session duration.** The service currently expires active sessions at the same
deadline as their invitation. Native `runHost()` derives service Register TTL
from invitation expiry (`reference_orchestrator.go:169`); service default/max TTL
is 600 seconds (`reference_service/webjam_reference/config.py:30`), and
`SessionRegistry.cleanup()`/`_is_expired()` remove even an enrolled, active session
once that deadline passes (`state.py:386`, `state.py:503`). Keepalives do not extend
the absolute expiry. A mountain-painting lesson or rehearsal cannot be certified
on that ten-minute ceiling. Separate the short one-use enrollment deadline from
a bounded authenticated active-session lifetime/renewal policy. Preserve idle
cleanup, replay tombstones, revocation, and caps; test beyond invitation expiry,
unjoined expiry, abandoned enrollment, stop/reset, and service restart. Do not
silently make invitation capabilities long-lived as a workaround.

**MTU.** Native QUIC starts at 1200 bytes with path-MTU discovery disabled
(`transport/internal/icequic/tls.go:208`). The reference relay adds 70 bytes.
That is at least 1298 bytes including IPv4/UDP headers, or 1318 for IPv6/UDP.
The relay contract's maximum 1350-byte inner packet would be 1448/1468 bytes.
Validate the real path without relying on unspecified fragmentation. In
particular, Tailscale documents a 1280-byte MTU, so even the minimum current
wrapped Initial exceeds it. This is a derived incompatibility risk, not a
physical failure measurement. A pilot needs a VPN with proven sufficient MTU,
or separately reviewed framing/fragmentation work with bounded reassembly and
DoS tests. A VPN toggle alone is not proof. [Official MTU documentation](https://tailscale.com/docs/reference/troubleshooting/network-configuration/tcp-connection-two-devices).

## Private overlay specifics

Do not recommend stock Tailscale plus the current Art invite as already working:
its default `100.64.0.0/10` addresses fail WebJam v2's RFC1918 check, Wi-Fi address
selection remains local, the native client is loopback-only, and the MTU concern
above remains. An RFC1918 VPN is also not automatically discovered by the host
and is not proof that its plaintext inner v2 traffic is safely confined.
[Official address-pool documentation](https://tailscale.com/docs/reference/ip-pool).

If an approved overlay is chosen, keep WebJam v3 encrypted end-to-end and expose
the service only on its private interface. Permit each participant only the
service's control/relay ports, not unrelated machines/subnets. Keep health on
loopback/private administration. Use a stable fully qualified service name and
trusted TLS, even inside the VPN. No exit node, public forwarding of v2, shared
account, or publicly discoverable room is needed.

Tailscale machine sharing requires recipient account acceptance and installed
clients, so this is additional first-use onboarding. Its coordination and fallback
relay are vendor services; an absolute ban covering *all* public rendezvous cannot
be assumed to allow them. A preconfigured private VPN with fixed peer endpoints
avoids dynamic public room lookup but still requires an approved VPN endpoint,
device enrollment, administrator maintenance, and performance proof.
[Official machine-sharing documentation](https://tailscale.com/kb/1084/sharing).

An overlay may use a direct or relayed path. Tailscale explicitly documents lower
latency for direct paths and possible relay fallback; WebJam must measure the
path it actually gets rather than label every encrypted connection suitable for
Music. [Official connection-types documentation](https://tailscale.com/docs/reference/connection-types).

## Deployment, platform, and evidence gates

For option A, identify an owner-operated host/VM, stable DNS, TLS certificate and
renewal owner, reachable TCP control and UDP relay ports, private health access,
patching and abuse-response owner, resource/bandwidth caps and real cost ceiling.
The current process has no shared durable session storage: restart/failover ends
sessions, and replicas need session-affine routing. Do not deploy the example
Compose file unchanged or buy infrastructure to discover these facts.

For option B, also identify the VPN/network owner, enrolled test devices, ACLs,
actual MTU, certificate trust, and a tested path to the private service. Confirm
network/account access with Jeff only after the implementation and test plan are
reviewable. No remote-host credentials are requested or stored in this artifact.

Current Host UI is macOS-only (`webjam_qt/windows/launch_dialog.py:410,917`). Four
desktop builds are not four supported hosting platforms. Frozen Windows v3
requires a valid Authenticode signature; frozen macOS verifies the bundled native
signature, hash, architecture, and build. Source checkouts and ad-hoc macOS lab
artifacts do not certify ordinary users' trust/install experience. Keep the
unsigned 0.27.2/signing gate Jeff-only; do not bypass checks for a convenient
pilot. Start with the exact OS pair Jeff authorizes and mark other pairs unproven.

Before real users: isolated service + two native processes must prove TLS failure
handling, admission rejection, enrollment/reset/replay, exact-pair isolation,
session duration, MTU/packet-size bounds, process interruption, and cleanup.
Then two separate physical home networks must prove invitation entry, room
profile/participant state, explicit Conversation handoff, sustained Art, Music
audibility and measured latency, loss/retry, and teardown. An Internet endpoint,
VPN status, successful build, or green lab test alone proves none of those full
user journeys.

## Specific decision to ask Jeff

May WebJam proceed with **design and isolated implementation/testing only** of a
self-operated, Internet-reachable v3 service whose sessions remain invitation-only,
as a narrow exception to the public-rendezvous hold? No deployment or spending
would be included. Alternatively, should the hold remain absolute and the first
two-home pilot require pre-enrolled private networking, with that extra setup
explicitly counted as a product limitation?
