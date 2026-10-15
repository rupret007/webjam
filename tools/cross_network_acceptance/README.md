# Cross-network acceptance (proof-only)

Three jobs must **PASS**. Any non-success is FAIL. No expected-FAIL taxonomy.

Proof peers drive real `webjam-fabric` via `TransportProcess` (same IPC as Qt:
`hello`/`ready`, `prepare_host`, `open_peer`, `send_help`, Jamulus loopback).
Qt is not started. Sidecar env is empty.

| Case | Proof |
| --- | --- |
| `same-lan` | Selected pair `host`. Sidecars + `webjam_reference` on the LAN. |
| `cone` | Dual-STUN EIM. DIRECT: pair is not relay, coturn `rb=`/`sb=` is 0. |
| `relay` | Dual-STUN EDM. Selected pair is relay **and** coturn logs `rb=`/`sb=`. |

Receipt `relay_bytes` is coturn session usage only. `loopback_shim_bytes.log`
is the userspace exact-pair counter and is never proof.

PASS needs Music and Art nonces both ways plus selected types from both peers.
`.PASS` is removed at start and written atomically after every assertion.

## Production paths

- `transport/cmd/webjam-fabric` (`-X main.buildID=<sha>`)
- `services.transport_runtime.TransportProcess`
- `transport/internal/ipc` runner + `referenceFabricOrchestrator`
- `transport/internal/icequic` QUIC over exact-pair `RelayPacketConn`
- compiled `reference-local` (`127.0.0.1:47131` / `47132`)
- `reference_service.webjam_reference` loopback
- `core.remote_invitation`
- `deploy.relay.turn_credentials.mint_coturn` and HTTP `POST /turn-credentials`

IPC is **reference-local**. `transport/internal/directstun` is on master
(#375) but not in `profile.Lookup`. Ordinary `direct-stun` Host/Join is
#376 and is not on master; cone and relay FAIL until that lands and this
harness switches to that IPC. Same-LAN may PASS on compiled loopback `host`.

**Not exercised:** Qt; `icequic.Agent`; public rendezvous; sidecar TURN
Allocate; hole-punch; coturn data relay.

Userspace loopback shim (not router DNAT) reaches compiled 127.0.0.1 sockets.
UDP is one `select` loop with unconnected inner sockets.

Cone SNAT is explicit (not MASQUERADE). `--random-fully` only if the probe
sees different ports. Routers default-DROP; ESTABLISHED,RELATED only back in.

Deferred: pion vnet; hairpin/mixed NATs; `direct-stun` IPC once on master.
Physical two-home Join, signing, notarization: **NOT RUN**.
