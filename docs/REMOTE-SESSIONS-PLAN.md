# Cross-network Host/Join plan

- Status: planning record for this PR (docs only; **no** public service, paid
  account, or new infrastructure is created here)
- Product use case: friends in **different houses / different networks**
- Musician constraint: **no port forwarding** if at all possible
- Related: [ADR 0001](adr/0001-remote-session-transport.md) (accepted v3
  sidecar design), [remote joining decision](plans/webjam-remote-joining-decision.md)
  (Internet direction authorized 2026-09-09), [threat model](security/remote-session-threat-model.md)

This document audits how Art and Music actually connect today, then picks
**one** way to make Copy Invite → Join work for a non-technical friend in
another house. Conversation (Webex / Zoom / Meet) already works across
networks; the gap is the WebJam room itself.

## What a first-time user is promised

Copy Invite now says:

> Copied. Send the whole message to your friend. Keep this window open.

That is the intended story. Same-Wi-Fi is **not** the normal path. A LAN
invite still carries an honest limitation in the pasted message when the
host is on the private v2 path:

> This invite only works on the host's Wi-Fi or local network. Keep this
> room open.

A missing local address still surfaces **Connect this Mac to Wi-Fi, then
try again** on the HUD, because WebJam cannot mint a LAN invite without a
private IPv4 address. That is a local-machine fact, not a claim that
friends must share Wi-Fi.

## Audit: how connections work today

There is no LAN discovery (no Bonjour / mDNS room list). The invitation
**is** discovery. Ordinary Host never opens a public Jamulus directory and
never asks a guest to type an IP or port.

### Music, ordinary Host (no lab flag)

| Piece | What actually happens |
| --- | --- |
| Who can host | Product door: **macOS only**. Windows/Linux ghost Host and tell the guest to join a Mac. |
| Live audio | Unmodified **Jamulus** UDP server, default port **22124**. LAN mode does **not** pass `--serverbindip`, so the OS binds the UDP listener on reachable interfaces (`services/bridge_service.py`). |
| How the guest finds the host | v2 `webjam://join?…` carrying a **literal RFC1918 IPv4**, Jamulus port, session id, peer HTTP port, and one-use bearer (`core/network_invite.py`). |
| Address chosen | `local_band_address()` prefers the Mac **Wi-Fi** IPv4 over VPN/Tailscale `utun` routes, then other RFC1918 addresses. Public IPs, hostnames, and IPv6 are rejected for v2. |
| Control / originals | Plaintext HTTP on the same RFC1918 address, **ephemeral port** (`SessionPeerServer(bind_host, 0, …)` in `core/session_transfer_runtime.py`). Bearer enrolls into that one session only. |
| NAT | The guest must be able to send UDP 22124 and TCP to `peer_port` at that private address. Another house cannot. Even if the host had a public IP, v2 **refuses** it so the recording plane never leaves RFC1918. |
| Conversation | Optional meeting URL in the same pasted message. That link is a separate app and already works across houses. |

### Art, ordinary Host

Same v2 invite and same RFC1918 HTTP control plane. Art **does not** start
Jamulus. Room facts (profile, start, Paint along identity, canvas handoff)
travel on the peer HTTP snapshot. Art Host is implemented on Windows,
Linux, and macOS. Cross-network reachability is identical to Music LAN:
**it does not work** for a friend in another house.

### v3 “remote” path (lab only)

This is the designed Internet path. It is **not** what ordinary Host/Join
uses.

| Piece | What actually happens |
| --- | --- |
| How a host opts in | Environment `WEBJAM_ENABLE_REFERENCE_LOCAL=1`. Not a user setting. |
| Invite | v3 `webjam://join?v=3&r=<profile>&i=<opaque envelope>`. Envelope has session/invite refs, one-use capability, expiry, host cert pin. **No address, port, name, or relay URL** ([ADR 0001](adr/0001-remote-session-transport.md)). |
| Compiled profile | Only `reference-local`: control TCP `127.0.0.1:47131` (plaintext because loopback), relay UDP `127.0.0.1:47132` (`core/rendezvous_profiles.py`). Go orchestrator **rejects** any other profile id and **rejects** a non-loopback relay destination. |
| Jamulus | Remote host mode adds `--serverbindip 127.0.0.1`. Live packets never leave the machine except inside the sidecar. |
| Data plane | Go sidecar: Jamulus UDP on loopback ↔ QUIC DATAGRAM; control/media on reliable QUIC streams. Native reference path uses an **exact-pair UDP relay**, not stock TURN. Pion ICE/STUN/TURN exists as a **separate lab** and is not the ordinary Host path. |
| Auth | One-use enrollment token (HKDF from the invite capability). Guest pins host SPKI from the invite. Mutually pinned QUIC TLS 1.3. Relay cannot decrypt audio. Reset Invite rotates the capability. |
| Proof | Isolated native + Python service tests exist. **Two homes, public DNS, packaged Internet Host: NOT RUN.** |

Changing a Python profile string cannot turn this on. The Go client must
dial TLS to a named host and open a routable UDP socket to a provisioned
relay. That work is listed in `docs/plans/webjam-remote-joining-decision.md`
and is still unshipped.

### What a friend in another house experiences today

1. Host copies an invite. HUD says send it to your friend.
2. Friend pastes it in Join.
3. If the invite is v2 (ordinary): Join tries the private IPv4 and fails.
   Troubleshooting may still mention the host’s Wi-Fi because that is the
   only network that invite can reach.
4. If the invite is v3 lab: both apps must reach loopback `reference-local`,
   which is the same computer or a CI lab, not two homes.
5. They can still talk in Conversation. They cannot share WebJam music or
   the Art room.

## Options (what we considered)

| Option | Musician setup | Port forward? | Paid infra? | Honest failure | Verdict |
| --- | --- | --- | --- | --- | --- |
| A. Keep v2 LAN | None | N/A | $0 | Other houses never join | Not the product |
| B. Tell users to port-forward 22124 + HTTP | Router admin, ISP CGNAT often blocks it | **Yes** | $0 | CGNAT / double-NAT still fail; public v2 HTTP is unsafe | Rejected |
| C. Public Jamulus directory servers | Guest picks a public server in Jamulus | No | $0 (community servers) | No private room, no WebJam invite, no recording plane, no Art, anyone can walk in | Rejected as Host/Join |
| D. WebRTC data/audio instead of Jamulus | None if it worked | No | Free STUN; relay often paid | Full WebRTC media stack fights Jamulus (second mixer/codec). STUN-only fails on symmetric NAT / CGNAT | Rejected as the audio engine. ICE may still be used later for traversal. |
| E. Tailscale / VPN overlay | Both people install and accept a machine share | No | Free for hobby; coordination is vendor-hosted | Extra first-run. Tailscale `100.64.0.0/10` fails v2 RFC1918 check. MTU can break current QUIC wrapping. Not one WebJam invite. | Rejected for v1 |
| F. **Existing v3 + owner-operated rendezvous/relay** | Install WebJam, paste invite | **No** | One small VM (see cost) | Without a reachable UDP relay, many home pairs cannot connect (see below) | **Selected** |
| G. v3 + STUN-only (no relay) | Same | No | $0 (public STUN) | Cone NAT sometimes works; **symmetric NAT, CGNAT, and many mobile ISPs fail** | Not reliable enough as the only path |
| H. Commercial TURN (Twilio, etc.) | Same | No | Metered, often $ tens+/month at music bitrate | Works, but paid vendor + extra policy proof ([threat model](security/remote-session-threat-model.md) requires exact-pair relay policy) | Fallback if owner VM is refused |

### What fails without a relay

Say it plainly:

- **v2 LAN invites fail 100%** across different houses. Private IPv4 is not
  routable on the public Internet, and WebJam will not put the plaintext
  control plane on a public IP.
- **STUN-only ICE** can connect two cone NATs. It **fails** when either side
  is symmetric NAT or CGNAT (common on consumer fiber, cable CGNAT, and
  cellular). Two such homes will sit on “joining…” forever.
- **UDP blocked** (some corporate/school networks, some guest Wi-Fi) fails
  both direct ICE and a UDP relay. A later TCP/TLS fallback would be extra
  work; it is not in the current sidecar.
- **Public Jamulus** can make sound across the Internet on a shared server,
  but it is not WebJam Host/Join and is not private.

A reachable **exact-pair UDP relay** is the reliability floor for “text your
friend in another house.” Direct ICE is a later latency optimization, not
the first ship.

## Recommendation (one approach)

**Finish and point ordinary Host/Join at the v3 transport we already have,
against a self-operated Internet rendezvous + exact-pair UDP relay.**

Why this, not a new stack:

- ADR 0001 already chose the Go sidecar, QUIC DATAGRAM for Jamulus, and
  exact-pair relay. Jeff authorized the Internet direction on 2026-09-09.
- The invite is already one opaque paste with no addresses for the guest to
  understand.
- Musicians do not port-forward. They do not install a VPN. They do not
  pick a public Jamulus server.
- Art and Music share the same enrollment; Art stays off Jamulus; Music
  Jamulus stays loopback-bound on each computer.
- No new paid vendor is required if the owner runs one small VM.

What this is **not**: a public room directory, short codes, or exposing
Jamulus/HTTP on the host’s WAN IP.

### Hosted server / paid service / who sets it up

| Item | Who | Monthly cost (order of magnitude) | Notes |
| --- | --- | --- | --- |
| Cloud VM for `reference_service` (TLS control + UDP relay + loopback health) | **Owner (Jeff)** provisions DNS, TLS cert, VM, firewall allowing TCP 443 (or 47131 with TLS) and UDP 47132 from the Internet | **About $5–6 / month** (Hetzner CX22 ~€4–5, DigitalOcean 1 GB droplet $6). Oracle/Google free-tier is possible but eviction-prone; do not bet a “final build” on it. | One region to start (pick closest to the first two homes). Restart **ends in-memory sessions** (current service has no durable store). |
| Public STUN | Nobody at WebJam | **$0** (e.g. existing public STUN) | Optional later for direct ICE. Not required to ship relayed Join. |
| Stock TURN vendor | — | Skip for v1 | Would add metered cost and a second exact-pair policy proof. |
| Cloudflare Tunnel / ngrok free | — | Skip | TCP-oriented; does not replace the UDP relay. |
| Musician router, VPN, or paid Tailscale | Musicians | **$0 and not asked** | Out of scope. |

Bandwidth to budget: Jamulus is typically tens of kb/s per musician, not
video. A 1 TB included-transfer VPS is ample for a two-person pilot. Watch
the cap if many concurrent rooms ever share one VM.

**If the owner will not run a VM:** the product cannot promise
cross-network Join. STUN-only and public Jamulus do not meet the private
one-invite bar. Do not ship a fake success path.

### Concrete build order (sized to ship soon)

Do these in order. Each step is independently testable. This PR does
**not** implement them.

1. **Immutable public profile** in Python and Go, separate from
   `reference-local`. Pin DNS name, TLS identity, relay host/port. Invites
   still carry only the profile id. Keep the loopback profile for CI.
2. **Native TLS 1.3 control dial** with verified chain and expected name.
   No `InsecureSkipVerify`, no guest-facing endpoint override. Isolated
   tests for wrong name, expired cert, timeout.
3. **Routable exact-pair UDP relay client**: outbound socket, only the
   provisioned relay, keep Jamulus on loopback. Preserve HMAC/replay/size/
   rate/generation checks already in the reference relay.
4. **Ordinary Host uses that profile** without
   `WEBJAM_ENABLE_REFERENCE_LOCAL`. Unavailable service fails with one
   musician-facing line, never falls back to public v2 HTTP or public
   Jamulus. Copy Invite stays the friend-facing HUD card.
5. **Host admission** before the VM is public: enrolled host device (mTLS
   or equivalent). Guests stay invitation-only. Existing one-use consume,
   Reset Invite, eight-hour room cap, 90s idle remain.
6. **Owner deploys** one VM + DNS + TLS. Document bind, firewall, and
   “restart kills rooms.” Do not publish the Compose example unchanged.
7. **Two-home proof** (required before claiming it works): Music audibility
   and measured latency; Art room state; paste-only invite; Reset Invite;
   Conversation still optional. Until that row is run, status is **NOT RUN**.
8. **Later, not blocking first ship:** Pion direct ICE + free STUN with
   relay fallback (lower latency when NAT allows); UDP-blocked TCP
   fallback; more than one guest.

Music Host remaining macOS-only is a separate product gate. Cross-network
Join should work for a Windows/Linux **guest** as soon as the v3 profile is
real; Windows/Linux **Music Host** is not unlocked by the relay.

## Security (invite secrets and encryption)

Keep the current v3 contract; do not weaken it to ship faster.

- Invitation is a **bearer secret** until enroll, reset, or expiry. Anyone
  with the unused paste can race the friend. Short TTL, one guest, Reset
  Invite, and visible roster bound that. Do not log the link.
- Service sees a **derived enrollment token**, never the raw capability.
  Signaling payloads stay AEAD-sealed.
- Live audio and control stay **end-to-end** in QUIC. The relay may see
  timing and size, not PCM or notes.
- v3 URLs never enter process arguments (`sys.argv` is v1/v2 only).
- Failure must not fall back to plaintext Internet v2.
- Exact-pair relay: no destination field, no arbitrary proxy.

Details: [threat model](security/remote-session-threat-model.md).

## This PR

- Retargeted Copy Invite HUD to the friend-facing card.
- LAN limitation stays in the pasted v2 message and in Join help, only
  when that invite cannot leave the host’s local network.
- **No** VM, DNS, TLS cert, paid account, or compiled public profile is
  created here.
