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

Both drive existing offscreen/headless Host and Join. **Art:** Qt conductor
(`QT_QPA_PLATFORM=offscreen`); guest must reach `ArtRoomState.CONNECTED` and
observe `paint_along`. **Music:** headless `HostPeerSession` /
`GuestPeerSession`; guest must enroll `creator_profile_key=music`. Neither
starts Jamulus, Webex, Drawpile, or a microphone. Session **state** is the
assertion.

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
        │  rendezvous TCP 47131, relay UDP 47132; STUN UDP 3478/19302
        │
  NAT-B 192.168.120.1 / 203.0.113.20   MASQUERADE, no DNAT
        │
  guest 192.168.120.10
```

WAN is TEST-NET-3 (`203.0.113.0/24`); home LANs stay RFC1918 so v2 invites
still name a private address. Optional `tc netem` (`100ms ± 50ms`, `1%` loss)
sits on each NAT WAN. Both homes must reach the public rendezvous while the
guest cannot TCP to the advertised private invite endpoint. v2 Host mints a
literal RFC1918 `webjam://join?…` and refuses public IPs; this harness never
adds a port-forward. Until ordinary Host/Join uses the public-ns
rendezvous/relay (`203.0.113.1:47131/47132`), cross-network stays FAIL.

## Receipts

JSON (`<sha>-<case>.json`) is always written. The `.PASS` sidecar is written
**only** on PASS. A later FAIL for the same SHA/case **removes** any
stale `.PASS` so the conductor cannot consume leftover success evidence.
Receipts never contain invitations, tokens, hosts, or peer ports.

```
webjam.cross-network-acceptance sha=<40-character-sha> status=PASS case=cross-network
```

`--expected-status FAIL` is green only for an honest classified
failure-to-connect (`harness_ok`). A crash after `guest.result`, timeout,
missing result, never-started process, programming/startup failure, host
crash after ready, connected guest that fails state exchange, or fake
connect still exits 1. Assertions are unchanged: the receipt stays FAIL.

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
# --output is resolved from the current directory, not from transport/.
for args in "--nat cone --profiles art" "--nat cone --profiles music" \
            "--nat symmetric --profiles art"; do
  sudo -E .venv/bin/python -m tools.cross_network_acceptance run \
    --case cross-network $args \
    --product-root "$PRODUCT" --transport-binary "$SIDECAR" \
    --output-dir out/cross-network-acceptance --no-netem --keep-workdir
done
```

`--nat cone` adds UDP NETMAP onto the real LAN endpoint
(`192.168.110.10/32`, `192.168.120.10/32`), not a /24 rewrite to
`192.168.120.20` and not a product `--dport` port-forward.
`--nat symmetric` uses `MASQUERADE --random-fully` on the host NAT. A guest
connect there is a **fake connect** and must fail the job even under
`--expected-status FAIL`.

These modes do **not** prove product cone-versus-symmetric NAT behavior while
packaged direct-stun discards TEST-NET-3 (`203.0.113.0/24`) STUN mappings.
Gather then advertises local-only candidates, so cone and symmetric fail for
the same reason. The harness still records topology, STUN reachability,
completed guest results, and fake-connect detection.

## Test-only STUN override (never in packaged builds)

PR #372 compiles `stun.cloudflare.com:3478` / `stun.l.google.com:19302` with
no env/config hook (`webjam-fabric` argv empty, `TransportProcess` `env: {}`).
This harness uses a **test-only** `/etc/netns/<ns>/hosts` map of those names
to `203.0.113.1` (RFC 5389 Binding) only while namespaces exist. Packaged
builds never read them. Do not add a packaged STUN URL setting.

Unit tests (no privileges):

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
  tests/test_cross_network_acceptance.py -q
```

## Workflow

`.github/workflows/cross-network-acceptance.yml` on `ubuntu-latest`:
`workflow_dispatch`, every push to `master`, and networking-path PRs. Same-LAN
must PASS. Two-NAT uses `--expected-status FAIL` (receipt FAIL until remote
sessions work; report-only `continue-on-error` on `master`). Runner setup:
netns probe (`ip netns add`), `linux-modules-extra-$(uname -r)` plus
`modprobe sch_netem`, and `coturn` (host unit stopped/masked). Receipts are
chowned for artifact upload. Not part of `WebJam CI`. After Internet Host/Join
lands, `workflow_dispatch` with `--expected-status PASS` records the
`<sha>-cross-network.PASS` artifact.

## Safety

- No port forwarding, public v2 HTTP, or required-check changes.
- Invitations stay in a 0600 file, never in argv, env, logs, or receipts.
- Physical two-home Internet Join remains **NOT RUN**.
