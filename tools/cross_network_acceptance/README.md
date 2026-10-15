# Cross-network acceptance (proof-only)

Three jobs must **PASS**. Any non-success is FAIL. No expected-FAIL taxonomy.

Proof peers drive real `webjam-fabric` via `TransportProcess` (same IPC as Qt:
`hello`/`ready`, `prepare_host`, `open_peer`, `send_help`, Jamulus loopback).
Qt is not started. Sidecar env is empty.

| Case | Proof |
| --- | --- |
| `same-lan` | Labels `host`. Sidecars + `webjam_reference` on the LAN. |
| `cone` | Dual-STUN EIM. Labels `srflx`. DIRECT fails if TURN or the compiled exact-pair shim carried any bytes (that is not a hole-punch). |
| `relay` | Dual-STUN EDM. Labels `relay`. Coturn on the public net. Harness POSTs `/turn-credentials` to `deploy/relay/turn_credentials.py` (`mint_coturn`). Relayed-byte proof is shim/coturn counters. |

PASS needs Music and Art nonces both ways plus path labels from both peers.
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

IPC is still **reference-local**. `transport/internal/directstun` is on master
(#375) but not selected by `profile.Lookup`. Ordinary `direct-stun` IPC is
#376 and is not stacked (diff budget). Labels `host`/`srflx`/`relay` are
topology-derived; this IPC path does not expose a Pion selected pair.

**Not exercised:** Qt; `icequic.Agent`; public rendezvous; sidecar TURN
Allocate (empty env; no `WEBJAM_TEST_TURN_*` on master).

Userspace loopback shim (not router DNAT) reaches compiled 127.0.0.1 sockets
across NAT. UDP is one `select` loop with unconnected inner sockets.

Cone SNAT is explicit (not MASQUERADE). `--random-fully` only if the probe
sees different ports. Routers default-DROP; ESTABLISHED,RELATED only back in.

Deferred: pion vnet; hairpin/mixed NATs; `direct-stun` IPC once on master.
Physical two-home Join, signing, notarization: **NOT RUN**.
