# Cross-network acceptance (proof-only)

Three jobs must **PASS**. Any non-success is FAIL.

Proof peers drive real `webjam-fabric` via `TransportProcess` and
`transport/internal/ipc`. Receipt `relay_bytes` is coturn `rb=`/`sb=` only.
`loopback_shim_bytes.log` is never proof.

| Case | Proof |
| --- | --- |
| `same-lan` | Sidecar types `host`. `webjam_reference` on the LAN. |
| `cone` | Dual-STUN EIM. DIRECT: sidecar pair is not relay, coturn usage is 0. |
| `relay` | Dual-STUN EDM. Sidecar pair is relay **and** coturn logs `rb=`/`sb=`. |

Compiled `reference-local` only. `directstun` is on master but not this IPC.
Cone and relay FAIL until `direct-stun` Host/Join is on master.
POST /turn-credentials uses `mint_coturn`. Deferred: pion vnet; hairpin.
Physical two-home Join: **NOT RUN**.
