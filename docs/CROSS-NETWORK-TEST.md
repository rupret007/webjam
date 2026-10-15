# Cross-network Host/Join acceptance

Proof-only Ubuntu netns Host/Join without port forwarding. Not a required
check. Peers run real `webjam-fabric` over compiled `reference-local` IPC.
Same-LAN, two cone NATs, and two symmetric NATs plus live
`POST /turn-credentials` must **PASS**. `probe-nat` fails closed. Cone SNAT
is explicit; dual-STUN is verified. Routers default-DROP. No DNAT/REJECT/ICMP.
Userspace shim reaches compiled loopback sockets. Labels are topology-derived.
Physical two-home Join is **NOT RUN**.

See `tools/cross_network_acceptance/README.md`. Deferred: pion vnet; hairpin.
