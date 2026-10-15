# Cross-network Host/Join acceptance

This is the automated proof that a guest in **another house** can join a
WebJam host **without port forwarding**. It runs on GitHub-hosted Ubuntu
runners using Linux network namespaces and iptables MASQUERADE.

It is **not** a required check. Do not add `Cross-network acceptance` to
branch protection. The required `WebJam CI` workflow is unchanged.

## What it proves

Two cases:

| Case | Topology | Expected today | When remote sessions ship |
| --- | --- | --- | --- |
| Same-LAN control | Host and guest on one RFC1918 L2, no NAT | **PASS** (ordinary v2 LAN) | Still PASS |
| Cross-network | Host behind NAT-A, guest behind NAT-B, public internet between them, **no port forward** | **FAIL** | **PASS** |

Both cases drive the existing offscreen/headless Host and Join paths:

- **Art:** Qt conductor (`QT_QPA_PLATFORM=offscreen`) → `start_lan_host` /
  `start_lan_guest`. The guest must reach `ArtRoomState.CONNECTED` and observe
  the host's `paint_along` start.
- **Music:** headless `HostPeerSession` / `GuestPeerSession` control plane
  (Linux Music Host stays ghosted at the product door). The guest must enroll
  and read `creator_profile_key=music`.

Neither case starts Jamulus audio, Webex, Drawpile, or a microphone. Session
**state** is the assertion, not audibility.

## Topology

```
Same-LAN control
  host 192.168.110.10 ── veth ── guest 192.168.110.11

Cross-network
  host 192.168.110.10
        │
  NAT-A 192.168.110.1 / 203.0.113.10   MASQUERADE, no DNAT
        │
  public internet 203.0.113.1
        │  rendezvous TCP 47131, relay UDP 47132
        │  real STUN UDP 3478 (and 19302 for the compiled Google fallback)
        │
  NAT-B 192.168.120.1 / 203.0.113.20   MASQUERADE, no DNAT
        │
  guest 192.168.120.10
```

The simulated WAN uses TEST-NET-3 (`203.0.113.0/24`). Home LANs stay RFC1918
so today's v2 invite still names a private address. Optional `tc netem`
(`100ms ± 50ms`, `1%` loss) sits on each NAT WAN.

The harness asserts that **both homes can reach the public rendezvous**
(outbound NAT works) while the guest **cannot** TCP to the host's advertised
private invite endpoint. That split is how we tell a broken topology from
the product gap.

## Why it fails today

Ordinary Host mints a v2 `webjam://join?…` with a **literal RFC1918 IPv4**,
Jamulus UDP 22124, and plaintext HTTP on an ephemeral peer port. v2 **refuses**
public IPs so that recording plane never leaves the LAN. Another house cannot
route to `192.168.110.10`, and this harness never adds a port-forward.

The compiled v3 profile is still **loopback-only** (`reference-local`).
Changing a Python string cannot turn Internet Join on. See
[REMOTE-SESSIONS-PLAN.md](REMOTE-SESSIONS-PLAN.md) in usability PR
[#372](https://github.com/rupret007/webjam/pull/372) (agent `bc-2df62653`),
[ADR 0001](adr/0001-remote-session-transport.md), and
[the remote-joining decision](plans/webjam-remote-joining-decision.md).

## What will make cross-network PASS

Finish ordinary Host/Join on the existing v3 sidecar against a reachable
rendezvous + exact-pair UDP relay, with **no musician port-forward**:

1. An immutable public profile (not `reference-local`) pinning DNS, TLS, and
   relay host/port. Invites still carry only the profile id.
2. Native TLS 1.3 control dial and a routable exact-pair UDP relay client.
   Jamulus stays loopback-bound on each computer.
3. Ordinary Host uses that profile without `WEBJAM_ENABLE_REFERENCE_LOCAL`.
   Unavailable service fails honestly; it must not fall back to public v2 HTTP
   or public Jamulus.
4. In this harness, run the same rendezvous/relay **in the public internet
   namespace** (`203.0.113.1:47131/47132`) instead of loopback. Both NATs
   already allow outbound TCP/UDP. No DNAT is added.

When the guest connects and exchanges session state on **master tip**, the
harness writes a conductor-consumable PASS file:

```
webjam.cross-network-acceptance sha=<40-character-sha> status=PASS case=cross-network
```

JSON receipts (`<sha>-<case>.json`) are always written. The `.PASS` sidecar is
written **only** on PASS. Receipts never contain invitations, tokens, hosts, or
peer ports.

## Run locally (Linux)

Needs `ip`, `iptables`, passwordless `sudo` for netns, and the repo venv.

```bash
# Control (must PASS today)
sudo -E .venv/bin/python -m tools.cross_network_acceptance run \
  --case same-lan --output-dir out/cross-network-acceptance \
  --sha "$(git rev-parse HEAD)" --no-netem

# Two NATs (FAIL today; PASS once v3 Internet Host/Join ships)
sudo -E .venv/bin/python -m tools.cross_network_acceptance run \
  --case cross-network --output-dir out/cross-network-acceptance \
  --sha "$(git rev-parse HEAD)" --netem

# PR #372 direct-stun (build sidecar, real STUN, cone vs symmetric)
PRODUCT=/path/to/pr-372
SIDECAR=out/cross-network-acceptance/webjam-fabric
.venv/bin/python -m tools.cross_network_acceptance build-sidecar \
  --product-root "$PRODUCT" --output "$SIDECAR"
sudo -E .venv/bin/python -m tools.cross_network_acceptance run \
  --case cross-network --nat cone --profiles art \
  --product-root "$PRODUCT" --transport-binary "$SIDECAR" \
  --output-dir out/cross-network-acceptance --no-netem --keep-workdir
sudo -E .venv/bin/python -m tools.cross_network_acceptance run \
  --case cross-network --nat cone --profiles music \
  --product-root "$PRODUCT" --transport-binary "$SIDECAR" \
  --output-dir out/cross-network-acceptance --no-netem --keep-workdir
sudo -E .venv/bin/python -m tools.cross_network_acceptance run \
  --case cross-network --nat symmetric --profiles art \
  --product-root "$PRODUCT" --transport-binary "$SIDECAR" \
  --output-dir out/cross-network-acceptance --no-netem --keep-workdir
```

`--nat cone` keeps iptables MASQUERADE and adds a UDP NETMAP reverse (full cone /
1:1 for the single LAN host). That is not a product `--dport` port-forward.
`--nat symmetric` uses `MASQUERADE --random-fully` on the host NAT so the STUN
mapping is destination-dependent; Join must fail honestly, not fake a connect.

## Test-only STUN override (never in packaged builds)

PR #372 compiles STUN servers in `transport/internal/directstun/path.go`
(`stun.cloudflare.com:3478`, `stun.l.google.com:19302`). There is **no**
product env/config hook: `webjam-fabric` accepts no argv, and
`TransportProcess` starts it with an empty environment (`env: {}`).

This harness therefore uses a **test-only** name override: `/etc/netns/<ns>/hosts`
maps those compiled names to `203.0.113.1`, where a real RFC 5389 Binding
server listens. The files exist only while the namespaces exist. Packaged
builds never read them, and the override is inactive unless this privileged
harness created the netns.

Do not add a packaged STUN URL setting. Pointing Host/Join at a lab STUN
server is a harness concern only.

macOS and Windows cannot create this topology; use the Ubuntu workflow.

Unit tests that do **not** need privileges (receipt format, no-DNAT plan):

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
  tests/test_cross_network_acceptance.py -q
```

## Workflow

`.github/workflows/cross-network-acceptance.yml`:

- `workflow_dispatch`
- pull requests that touch networking paths (invite, session transfer,
  rendezvous, LAN guest, transport, this harness)
- two jobs: **Same-LAN control** (must be green) and **Cross-network (two NATs)**
  (red until remote sessions work)
- not part of `WebJam CI`; not a required check

To record a conductor PASS against master tip after Internet Host/Join lands,
run **Cross-network acceptance** with `workflow_dispatch` on that SHA. The
`<sha>-cross-network.PASS` artifact is the receipt.

## Safety

- No port forwarding, public v2 HTTP, or required-check changes.
- Invitations stay in a 0600 file on the shared disk, never in argv, env, logs,
  or receipts.
- Physical two-home Internet Join remains **NOT RUN** until observed on real
  ISPs. This harness is the automated stand-in, not that row.
