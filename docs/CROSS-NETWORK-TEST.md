# Cross-network Host/Join

Proof-only `direct-stun`. `relay_bytes` is coturn peer `rb=`/`sb=` scoped to the
current Host/Join allocations by the run's fresh TURN user identity and
successful ALLOCATE/CHANNEL_BIND records. Client-side usage and other runs do
not count; zero scoped bytes fails relay acceptance. See the
[harness accounting contract](../tools/cross_network_acceptance/README.md).
Physical two-home Join is **NOT RUN**.
