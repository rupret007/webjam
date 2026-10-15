# Cross-network Host/Join acceptance

Proof-only Ubuntu netns Host/Join **without port forwarding**. Not a required
check. Same-LAN, two cone NATs (direct ICE pair), and two symmetric NATs plus
coturn on the public net (relay pair + coturn byte counters) must **PASS**.
Any non-success is FAIL. Privileged `probe-nat` fails closed. Cone routers use
explicit SNAT; dual-STUN mapping is verified before each case. Routers
default-DROP INPUT/FORWARD. No DNAT, NETMAP, REDIRECT, REJECT, or ICMP
unreachable.

See `tools/cross_network_acceptance/README.md`. Deferred: pion vnet in-process
tier; hairpin and mixed cone/symmetric cases. Physical two-home Join is
**NOT RUN**.
