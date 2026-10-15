# Cross-network Host/Join acceptance

Proof-only Ubuntu netns Host/Join without port forwarding. Not a required
check. Peers run real `webjam-fabric` over compiled `reference-local` IPC.
Same-LAN may PASS. Cone DIRECT and relay VIA coturn FAIL until `direct-stun`
Host/Join is on master and this harness switches to that IPC. Receipt
`relay_bytes` is coturn `rb=`/`sb=` only. Physical two-home Join is **NOT RUN**.

See `tools/cross_network_acceptance/README.md`. Deferred: pion vnet; hairpin.
