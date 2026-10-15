# Cross-network Host/Join acceptance

Optional Ubuntu netns Host/Join **without port forwarding**. Not a required
check. Same-LAN (`192.168.110.10`—`192.168.110.11`) **PASS**. Two-NAT **FAIL**
until Internet Join. Privileged `probe-nat` fails closed. No DNAT, NETMAP,
REDIRECT, or `--dport`. `masquerade` / `cone`: RFC 4787 endpoint-independent
mapping + address-and-port-dependent filtering. `symmetric`: endpoint-dependent
mapping (`--random-fully`). A **fake connect** fails the job. These modes
do **not** prove product cone-versus-symmetric NAT behavior while packaged
direct-stun discards TEST-NET-3. JSON always; a later FAIL removes a
stale `.PASS`. `--expected-status FAIL` is green only for a finished
network-unreachable connect.

SIDECAR=out/cross-network-acceptance/webjam-fabric
python -m tools.cross_network_acceptance build-sidecar --product-root /path/to/pr-372 --output "$SIDECAR"

`--output` is from cwd, not `transport/`. **Test-only** STUN (never in packaged
builds) maps `stun.cloudflare.com` / `stun.l.google.com` to `203.0.113.1`. Physical
two-home Join is **NOT RUN**.
