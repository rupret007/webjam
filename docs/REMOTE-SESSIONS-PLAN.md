# Cross-network Host/Join plan

- Status: **this PR ships ordinary Host/Join on `direct-stun`** when the
  bundled sidecar is present. No paid service. No user port forwarding.
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
| Packaged path | If `webjam-fabric` is present, Host uses compiled **`direct-stun`**: Jamulus stays loopback; the sidecar binds UDP, talks to public STUN, and seals candidates on the clipboard. |
| Source-checkout fallback | Without a sidecar binary, Host stays on **v2 LAN**: unmodified Jamulus UDP **22124**, v2 `webjam://join?…` with a literal RFC1918 IPv4, peer HTTP on an ephemeral port. |
| NAT (direct-stun) | Guest dials sealed host/srflx candidates. Cone NAT and same LAN can connect. Symmetric NAT / CGNAT / cellular often cannot, with no relay. |
| NAT (v2 LAN) | Another house cannot reach RFC1918. v2 still refuses public IPs. |
| Conversation | Optional meeting URL in the same pasted message. That link is a separate app and already works across houses. |

### Art, ordinary Host

Same split as Music: packaged `direct-stun` when the sidecar exists; v2 LAN
otherwise. Art **does not** start Jamulus. Room facts travel on the native
QUIC room channel (direct-stun) or the LAN peer HTTP snapshot (v2). Art Host
is implemented on Windows, Linux, and macOS.

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

### What a friend in another house experiences

1. Host copies an invite. HUD says send it to your friend.
2. Friend pastes the whole message in Join (URL plus `webjam-signal:` on
   packaged `direct-stun`).
3. If STUN mapped a public port and NAT is cone-like, they Join. If either
   home is CGNAT/symmetric/cellular, Join fails with a home-Wi-Fi line.
4. Source checkouts without a sidecar still mint v2 LAN invites, which
   another house cannot use.
5. v3 lab (`WEBJAM_ENABLE_REFERENCE_LOCAL=1`) is still loopback
   `reference-local`, not two homes.
6. Conversation can still work while the WebJam room cannot.

## Options (what we considered)

| Option | Musician setup | Port forward? | Paid infra? | Honest failure | Verdict |
| --- | --- | --- | --- | --- | --- |
| A. Keep v2 LAN | None | N/A | $0 | Other houses never join | Not the product |
| B. Tell users to port-forward 22124 + HTTP | Router admin, ISP CGNAT often blocks it | **Yes** | $0 | CGNAT / double-NAT still fail; public v2 HTTP is unsafe | Rejected |
| C. Public Jamulus directory servers | Guest picks a public server in Jamulus | No | $0 (community servers) | No private room, no WebJam invite, no recording plane, no Art, anyone can walk in | Rejected as Host/Join |
| D. WebRTC data/audio instead of Jamulus | None if it worked | No | Free STUN; relay often paid | Full WebRTC media stack fights Jamulus (second mixer/codec). STUN-only fails on symmetric NAT / CGNAT | Rejected as the audio engine. ICE may still be used later for traversal. |
| E. Tailscale / VPN overlay | Both people install and accept a machine share | No | Free for hobby; coordination is vendor-hosted | Extra first-run. Tailscale `100.64.0.0/10` fails v2 RFC1918 check. MTU can break current QUIC wrapping. Not one WebJam invite. | Rejected for v1 |
| F. Existing v3 + owner-operated rendezvous/relay | Install WebJam, paste invite | **No** | One small VM (see cost) | Without a reachable UDP relay, many home pairs cannot connect (see below) | Later reliability floor, not this ship |
| G. **v3 `direct-stun` + public STUN + clipboard-sealed candidates** | Same | **No** | **$0** | Cone NAT / same LAN work; **symmetric NAT, CGNAT, and many mobile ISPs fail** | **Selected for this build** |
| H. Commercial TURN (Twilio, etc.) | Same | No | Metered, often $ tens+/month at music bitrate | Works, but paid vendor + extra policy proof ([threat model](security/remote-session-threat-model.md) requires exact-pair relay policy) | Skip |

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

A reachable **exact-pair UDP relay** is still the reliability floor for
“every pair of homes, including CGNAT and cellular.” This build ships the
**$0 STUN path** first so friends can Join with no owner VM and no port
forwarding. Pairs that fail NAT get one honest line, not a fake success.

## Recommendation (one approach)

**Ordinary Host/Join uses compiled profile `direct-stun`: public STUN, QUIC
over the host’s UDP socket, sealed candidate bundle on the clipboard (not
in the v3 URL).**

Why this, not a paid VM, for this ship:

- Musicians do not port-forward. They do not install a VPN. They do not
  pick a public Jamulus server.
- There is **no hosted server to provision** and **$0 / month**.
- The v3 URL stays opaque (ADR 0001). Addresses travel only inside a sealed
  `webjam-signal:` clipboard line the friend pastes as part of the same
  message.
- Same-LAN still works via RFC1918 host candidates. Other houses work when
  STUN maps a cone NAT. Art and Music share enrollment; Jamulus stays
  loopback-bound on each computer.
- Source checkouts without `webjam-fabric` keep v2 LAN Host. Packaged apps
  that include the sidecar use `direct-stun` without the lab env flag.

What this is **not**: a public room directory, short codes, exposing
Jamulus/HTTP on the WAN IP, or a promise that CGNAT/cellular always Join.

### Hosted server / paid service / who sets it up

| Item | Who | Monthly cost | Notes |
| --- | --- | --- | --- |
| Public STUN (`stun.cloudflare.com:3478`, `stun.l.google.com:19302`) | Nobody at WebJam | **$0** | Compiled into the sidecar. No account. |
| Owner VM / `reference_service` | — | **$0 this ship** | Not required to Host/Join. Still the later reliability floor for CGNAT. About $5–6/month (Hetzner CX22 / DigitalOcean 1 GB) **if** Jeff later wants “almost every home pair.” |
| Stock TURN vendor | — | Skip | Metered; extra exact-pair policy proof. |
| Cloudflare Tunnel / ngrok | — | Skip | TCP-oriented; does not replace UDP. |
| Musician router, VPN, Tailscale | Musicians | **$0 and not asked** | Out of scope. |

**What fails without a relay (honest):**

- Host behind **symmetric NAT or CGNAT**: STUN’s mapped port is not the
  port that receives the guest. Join sits on connecting, then the HUD says
  to use home Wi-Fi and ask for a fresh invite.
- **Cellular / many phone hotspots**: same failure.
- **UDP blocked** (some school/work Wi-Fi): both STUN and QUIC fail.
- **v2 LAN invites** still fail 100% across houses. Direct-stun is the
  ordinary packaged path; LAN remains the no-sidecar fallback.

Do not treat a failed Join as success. Conversation (Webex/Zoom/Meet) can
still work while the WebJam room cannot.

### Concrete build order (this PR, sized to ship)

1. **Compiled `direct-stun` profile** in Python and Go, beside
   `reference-local`. No DNS, TLS control, or relay endpoints.
2. **Sidecar orchestrator**: bind UDP, STUN, seal WJSE candidates, QUIC
   listen/dial, exporter-bound proofs on an enrollment stream. Isolated
   loopback host/guest test.
3. **Clipboard signaling**: Copy Invite appends `webjam-signal:`; Join paste
   requires it for `direct-stun`. v3 URL still has no addresses.
4. **Ordinary Host** uses `direct-stun` when `webjam-fabric` is present.
   Lab flag still selects `reference-local`. Missing sidecar stays v2 LAN
   with the honest Wi-Fi line in the paste.
5. **Musician copy**: friend-facing HUD card unchanged. LAN-only STUN
   failure keeps the Wi-Fi sentence in the paste. Join timeout names home
   Wi-Fi, not a fake connected room.
6. **Two-home proof** (required before claiming it always works): Music
   audibility and Art room state across two houses; Reset Invite; CGNAT
   pair expected to fail. Until that row is run, status is **NOT RUN**.
7. **Later, not blocking this ship:** owner VM exact-pair relay as fallback
   when STUN cannot map; UDP-blocked TCP; more than one guest.

Music Host remaining macOS-only is a separate product gate. A Windows/Linux
**guest** can Join a Mac Music host on `direct-stun`. Windows/Linux **Art
Host** uses the same profile when the sidecar is present.

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
- LAN limitation stays in the pasted message only when the share cannot
  leave the host’s local network (v2 LAN, or `direct-stun` without a STUN
  mapping).
- Ships compiled `direct-stun`, clipboard-sealed candidates, and ordinary
  Host/Join on that profile when `webjam-fabric` is in the package.
- **No** VM, DNS, TLS cert, or paid account is created here. Two-home
  Internet proof remains **NOT RUN**.
