# Cross-network Host/Join acceptance

Optional Ubuntu proof that a guest in another house can join a WebJam host
**without port forwarding**. Uses Linux netns and iptables MASQUERADE.

It is **not** a required check. Do not add `Cross-network acceptance` to
branch protection. `WebJam CI` is unchanged.

## Cases

| Case | Topology | Expected today |
| --- | --- | --- |
| Same-LAN control | Host and guest on one RFC1918 L2, no NAT | **PASS** |
| Cross-network | Host NAT-A, guest NAT-B, public internet, **no port forward** | **FAIL** until Internet Join |

Both drive existing offscreen/headless Host and Join:

- **Art:** Qt conductor (`QT_QPA_PLATFORM=offscreen`) → `start_lan_host` /
  `start_lan_guest`. Guest must reach `ArtRoomState.CONNECTED` and observe
  the host's `paint_along` start.
- **Music:** headless `HostPeerSession` / `GuestPeerSession` (Linux Music Host
  stays ghosted at the product door). Guest must enroll with
  `creator_profile_key=music`.

Neither starts Jamulus audio, Webex, Drawpile, or a microphone. Session
**state** is the assertion.

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

WAN is TEST-NET-3 (`203.0.113.0/24`); home LANs stay RFC1918 so v2 invites
still name a private address. Optional `tc netem` (`100ms ± 50ms`, `1%` loss)
sits on each NAT WAN.

The harness asserts that **both homes can reach the public rendezvous**
(outbound NAT works) while the guest **cannot** TCP to the host's advertised
private invite endpoint. That split distinguishes a broken topology from the
product gap. v2 Host mints a literal RFC1918 `webjam://join?…` and refuses
public IPs; this harness never adds a port-forward. Until ordinary Host/Join
uses the public-ns rendezvous/relay (`203.0.113.1:47131/47132`) on master tip,
cross-network stays FAIL.

## Receipts

JSON (`<sha>-<case>.json`) is always written. The `.PASS` sidecar is written
**only** on PASS. A later FAIL for the same SHA/case **removes** any
stale `.PASS` so the conductor cannot consume leftover success evidence.
Receipts never contain invitations, tokens, hosts, or peer ports.

```
webjam.cross-network-acceptance sha=<40-character-sha> status=PASS case=cross-network
```

`--expected-status FAIL` keeps the GitHub job green only when that FAIL is an
honest completed guest result (`harness_ok`, no timeout/crash/missing result,
no fake connect). Unexpected PASS, a broken harness, or a fake connect still
exits 1. Assertions are unchanged: the receipt stays FAIL.

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
for args in "--nat cone --profiles art" "--nat cone --profiles music" \
            "--nat symmetric --profiles art"; do
  sudo -E .venv/bin/python -m tools.cross_network_acceptance run \
    --case cross-network $args \
    --product-root "$PRODUCT" --transport-binary "$SIDECAR" \
    --output-dir out/cross-network-acceptance --no-netem --keep-workdir
done
```

`--nat cone` keeps iptables MASQUERADE and adds a UDP NETMAP reverse onto the
**actual** LAN endpoint (`192.168.110.10/32` on NAT-A, `192.168.120.10/32` on
NAT-B). Mapping the /24 while preserving WAN host bits would send inbound
guest traffic to `192.168.120.20`, which is not the guest. That NETMAP is
full cone / 1:1 for the single endpoint, not a product `--dport` port-forward.
`--nat symmetric` uses `MASQUERADE --random-fully` on the host NAT. A guest
connect there is a **fake connect** and must fail the job even under
`--expected-status FAIL`.

These modes do **not** prove product cone-versus-symmetric NAT behavior while
packaged direct-stun discards TEST-NET-3 (`203.0.113.0/24`) STUN mappings.
Gather then advertises local-only candidates, so cone and symmetric fail for
the same reason. The harness still records topology, STUN reachability,
completed guest results, and fake-connect detection.

## Test-only STUN override (never in packaged builds)

PR #372 compiles STUN servers in `transport/internal/directstun/path.go`
(`stun.cloudflare.com:3478`, `stun.l.google.com:19302`). There is **no**
product env/config hook: `webjam-fabric` accepts no argv, and
`TransportProcess` starts it with an empty environment (`env: {}`).

This harness uses a **test-only** name override: `/etc/netns/<ns>/hosts`
maps those compiled names to `203.0.113.1`, where a real RFC 5389 Binding
server listens. The files exist only while the namespaces exist. Packaged
builds never read them.

Do not add a packaged STUN URL setting. macOS and Windows cannot create this
topology; use the Ubuntu workflow.

Unit tests (no privileges):

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
  tests/test_cross_network_acceptance.py -q
```

## Workflow

`.github/workflows/cross-network-acceptance.yml` on GitHub-hosted
`ubuntu-latest`: `workflow_dispatch`, every push to `master`, and pull
requests that touch networking paths. Two jobs: **Same-LAN control** (must
PASS) and **Cross-network (two NATs)** (receipt **FAIL** until remote
sessions work). The two-NAT job uses `--expected-status FAIL` so an honest
FAIL is a green step. On `master` that job is report-only
(`continue-on-error`). Networking PRs must keep it green (honest FAIL today;
flip `--expected-status` to PASS when Internet Join actually connects).

Runner setup: netns probe (`ip netns add`), `linux-modules-extra-$(uname -r)`
plus `modprobe sch_netem`, and `coturn` (host unit stopped/masked; `turnserver`
in the public namespace, Python Binding fallback). Receipts are chowned back
to the runner so artifact upload can read the 0600 files. Not part of
`WebJam CI`.

To record a conductor PASS against master tip after Internet Host/Join lands,
run **Cross-network acceptance** with `workflow_dispatch` on that SHA and
`--expected-status PASS`. The `<sha>-cross-network.PASS` artifact is the
receipt.

## Safety

- No port forwarding, public v2 HTTP, or required-check changes.
- Invitations stay in a 0600 file on the shared disk, never in argv, env, logs,
  or receipts.
- Physical two-home Internet Join remains **NOT RUN** until observed on real
  ISPs.
