# Cross-network acceptance (proof-only)

Three jobs must **PASS**. Any non-success is FAIL. There is no expected-FAIL
and no honest-unreachable taxonomy.

Proof peers drive the **real** `webjam-fabric` sidecar through
`services.transport_runtime.TransportProcess` (the same JSON-lines IPC the Qt
desktop uses: `hello`/`ready`, `prepare_host`, `open_peer`, `send_help`,
Jamulus loopback UDP). Qt itself is not started.

| Case | Proof |
| --- | --- |
| `same-lan` | Host/host labels. Both sidecars plus `webjam_reference` on the LAN; guest reaches the compiled loopback via a userspace shim (not router DNAT). |
| `cone` | Two endpoint-independent NATs (dual-STUN verified). Labels are **srflx**. Compiled fabric still uses the exact-pair relay; this is **not** a Pion ICE selected pair. TURN is not used. |
| `relay` | Two symmetric NATs plus coturn on the public net. Labels are **relay**. Sidecars still use the compiled exact-pair relay. `deploy.relay.turn_credentials.post_turn_credentials` mints the `POST /turn-credentials` body (same helper as `deploy/relay`). Relayed-byte proof is shim/coturn counters on the path that carried the sidecar payloads. |

PASS requires Music (Jamulus datagrams on the sidecar loopback) and Art
(`send_help` nonce) both ways, plus path labels from **both** peers. Missing
or unknown fields fail. Receipt `.PASS` is deleted at start and written
atomically only after every assertion.

## Production code paths exercised

- `transport/cmd/webjam-fabric` (built with `-X main.buildID=<sha>`)
- `transport/internal/ipc` runner + `referenceFabricOrchestrator`
- `transport/internal/icequic` QUIC over the exact-pair `RelayPacketConn`
  (not `icequic.Agent` gathering)
- `transport/internal/profile` compiled `reference-local` only
  (`127.0.0.1:47131` control, `127.0.0.1:47132` relay)
- `reference_service.webjam_reference` loopback bind (public bind still
  requires TLS + host admission; the harness never loosens that)
- `core.remote_invitation.issue_remote_invitation` /
  `parse_remote_invitation_link`
- `deploy.relay.turn_credentials` POST `/turn-credentials` mint

**Not exercised:** Qt UI; Pion ICE Agent gathering (`icequic.Agent` remains
`transport/internal/icequic/lab_test.go`); a public rendezvous profile.

NAT mapping is probed (one inside socket, two STUN endpoints) before each
cross-network case. Cone routers use explicit SNAT (not MASQUERADE). Symmetric
`--random-fully` is accepted only if the probe sees different ports. Routers
default-DROP INPUT/FORWARD and allow only ESTABLISHED,RELATED back in.

Deferred (follow-up): pion vnet in-process tier; hairpin and mixed cone/symmetric
cases; compiling an ICE profile into sidecar IPC.

Physical two-home Join, signing, and notarization are **NOT RUN**.
