# Cross-network acceptance (proof-only)

Three jobs must **PASS**. Peers drive `webjam-fabric` via `TransportProcess`
and `transport/internal/ipc` `direct-stun` (`directstun`, not
`reference-local`). Receipt `relay_bytes` is coturn `rb=`/`sb=` only.
`same-lan`/`cone`: `session_path=direct` and coturn 0. `relay`:
`session_path=relay` and coturn `rb=`/`sb=`. Dual-STUN.
`WEBJAM_TEST_HARNESS=1`, `WEBJAM_TEST_STUN_SERVER`; relay also
`WEBJAM_TEST_TURN_SERVER`, `WEBJAM_TEST_TURN_CREDENTIAL_URL`.
POST /turn-credentials. Deferred: pion vnet; hairpin. **NOT RUN**.
