# Cross-network Host/Join acceptance

Proof-only Ubuntu netns Host/Join **without port forwarding**. Not a required
check. Peers run the real `webjam-fabric` sidecar over compiled
`reference-local` IPC. Same-LAN, two cone NATs, and two symmetric NATs plus
the POST `/turn-credentials` mint must **PASS**. Any non-success is FAIL.
Privileged `probe-nat` fails closed. Cone routers use explicit SNAT; dual-STUN
mapping is verified before each case. Routers default-DROP INPUT/FORWARD. No
DNAT, NETMAP, REDIRECT, REJECT, or ICMP unreachable on the NAT routers.
Loopback-to-public forwarding uses a userspace shim because the compiled
sidecar cannot be given a public address.

See `tools/cross_network_acceptance/README.md` for the exact production paths.
Deferred: pion vnet in-process tier; hairpin and mixed cone/symmetric cases;
Pion ICE pairs on the IPC path. Physical two-home Join is **NOT RUN**.
