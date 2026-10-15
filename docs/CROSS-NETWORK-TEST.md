# Cross-network Host/Join acceptance

Optional Ubuntu proof that a guest in another house can join a WebJam host
**without port forwarding**. Linux netns + iptables MASQUERADE. **Not** a
required check; `WebJam CI` is unchanged.

Same-LAN (`192.168.110.10`—`192.168.110.11`): **PASS**. Cross-network (host
NAT-A `203.0.113.10`, inet `203.0.113.1` rendezvous 47131 / STUN 3478+19302,
guest NAT-B `203.0.113.20` / `192.168.120.10`, no port forward): **FAIL** until
Internet Join. Art: offscreen Qt, `ArtRoomState.CONNECTED` + `paint_along`.
Music: `creator_profile_key=music`. No Jamulus/Webex/Drawpile/mic.

MASQUERADE only; no DNAT. TEST-NET-3 keeps v2 invites RFC1918. Optional
`tc netem` (`100ms ± 50ms`, `1%` loss). Cone NETMAP (`192.168.110.10/32`,
`192.168.120.10/32`) is conntrack-gated: unsolicited inbound UDP is dropped
until outbound traffic creates a mapping—not a /24 rewrite to `192.168.120.20`
or a `--dport` port-forward. Symmetric host `MASQUERADE --random-fully` is a
**fake connect** and fails the job even under `--expected-status FAIL`. These
modes do **not** prove product cone-versus-symmetric NAT behavior while
packaged direct-stun discards TEST-NET-3.

JSON always; `.PASS` only on PASS. A later FAIL removes a stale `.PASS`.

```
webjam.cross-network-acceptance sha=<40-character-sha> status=PASS case=cross-network
```

`--expected-status FAIL` is green only for an honest classified
failure-to-connect (`harness_ok`). Crash, timeout, missing result, host crash
after ready/reap, transport-connected without state exchange, or fake connect
still exits 1. Receipt stays FAIL.

## Run locally (Linux)

```bash
sudo -E .venv/bin/python -m tools.cross_network_acceptance run \
  --case same-lan --output-dir out/cross-network-acceptance \
  --sha "$(git rev-parse HEAD)" --no-netem
sudo -E .venv/bin/python -m tools.cross_network_acceptance run \
  --case cross-network --output-dir out/cross-network-acceptance \
  --sha "$(git rev-parse HEAD)" --netem
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
`--output` is resolved from the current directory, not from `transport/`.

## Test-only STUN override (never in packaged builds)

PR #372 compiles `stun.cloudflare.com:3478` / `stun.l.google.com:19302` with
no env/config hook. A **test-only** `/etc/netns/<ns>/hosts` map points those
names at `203.0.113.1` only while namespaces exist. Packaged builds never
read them.

`.github/workflows/cross-network-acceptance.yml` on `ubuntu-latest`. Same-LAN
must PASS. Two-NAT uses `--expected-status FAIL` (`continue-on-error` on
`master`). Not part of `WebJam CI`. Invitations stay in a 0600 file. Physical
two-home Internet Join remains **NOT RUN**.
