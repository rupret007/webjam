# Cross-network Host/Join acceptance

Optional Ubuntu proof that a guest in another house can join a WebJam host
**without port forwarding**. Linux netns + iptables. **Not** a required
check; `WebJam CI` is unchanged.

Same-LAN (`192.168.110.10`—`192.168.110.11`): **PASS**. Cross-network (host
NAT-A `203.0.113.10`, inet `203.0.113.1` rendezvous 47131 / STUN 3478+19302,
guest NAT-B `203.0.113.20` / `192.168.120.10`): **FAIL** until Internet Join.
Art: offscreen Qt, `ArtRoomState.CONNECTED` + `paint_along`. Music:
`creator_profile_key=music`. No Jamulus/Webex/Drawpile/mic.

## NAT model (RFC 4787)

No DNAT, NETMAP, REDIRECT, or `--dport`. Privileged CI runs `probe-nat`
(unsolicited drop, reply from the contacted endpoint, mapping reuse)
and fails closed.

- `masquerade` / `cone`: endpoint-independent mapping + address-and-port-dependent
  filtering (port-restricted cone). One internal socket reuses one external
  port. A peer is admitted only after the inside host sent to that address:port.
  Unsolicited UDP from a never-contacted peer is dropped.
- `symmetric`: endpoint-dependent mapping on NAT-A (`--random-fully`) + the
  same filter. Mapping is not reused. A connect is a **fake connect** and
  fails the job even under `--expected-status FAIL`. Guest stays cone.

These modes do **not** prove product cone-versus-symmetric NAT behavior while
packaged direct-stun discards TEST-NET-3. Optional `tc netem` (`100ms ± 50ms`,
`1%` loss). JSON always; `.PASS` only on PASS. A later FAIL removes a stale `.PASS`.

```
webjam.cross-network-acceptance sha=<40-character-sha> status=PASS case=cross-network
```

`--expected-status FAIL` is green only for an honest classified
failure-to-connect (`harness_ok`). Crash (including exit 1 after a success
`guest.result`), timeout, missing result, host crash after ready/reap,
transport-connected without state exchange, or fake connect still exits 1.

## Run locally (Linux)

```bash
sudo -E .venv/bin/python -m tools.cross_network_acceptance probe-nat
sudo -E .venv/bin/python -m tools.cross_network_acceptance run \
  --case same-lan --output-dir out/cross-network-acceptance \
  --sha "$(git rev-parse HEAD)" --no-netem
PRODUCT=/path/to/pr-372
SIDECAR=out/cross-network-acceptance/webjam-fabric
.venv/bin/python -m tools.cross_network_acceptance build-sidecar \
  --product-root "$PRODUCT" --output "$SIDECAR"
sudo -E .venv/bin/python -m tools.cross_network_acceptance run \
  --case cross-network --nat cone --profiles art \
  --product-root "$PRODUCT" --transport-binary "$SIDECAR" \
  --output-dir out/cross-network-acceptance --no-netem --keep-workdir
```
`--output` is resolved from the current directory, not from `transport/`.

## Test-only STUN override (never in packaged builds)

PR #372 compiles `stun.cloudflare.com:3478` / `stun.l.google.com:19302` with
no env/config hook. A **test-only** `/etc/netns/<ns>/hosts` map points those
names at `203.0.113.1` only while namespaces exist. Packaged builds never
read them.

`.github/workflows/cross-network-acceptance.yml` on `ubuntu-latest`. Same-LAN
must PASS. Two-NAT uses `--expected-status FAIL` (`continue-on-error` on
`master`). Not part of `WebJam CI`. Physical two-home Join is **NOT RUN**.
