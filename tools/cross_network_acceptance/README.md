# Cross-network acceptance (proof-only)

Three jobs must **PASS**. Peers drive `webjam-fabric` via `TransportProcess`
and `transport/internal/ipc` `direct-stun` (`directstun`, not
`reference-local`). Receipt `relay_bytes` sums coturn `peer usage:` `rb=`/`sb=`
only for the proof run's allocations. The isolated credential server uses a
fresh random `webjam-proof-…` user suffix for each setup attempt; only the
Host/Join proof peers use its credentials to allocate. The parser requires
successful ALLOCATE and CHANNEL_BIND records with the same session ID, full
username, and `webjam` realm before counting peer usage. Unrelated or previous
runs, malformed records, and client-side `usage:` contribute zero. Harvesting
waits for scoped usage, so unrelated traffic cannot end the wait early.

Coturn resets these counters after each usage dump; intervals are summed.
This is aggregate traffic at the selected allocations, not unique application
payload bytes (a relay-to-relay packet traverses both allocations). The
[coturn usage implementation](https://github.com/coturn/coturn/blob/4.6.1/src/apps/relay/ns_ioalib_engine_impl.c#L3808)
defines the counter semantics. Missing scope or zero scoped traffic fails the
relay proof even if both sidecars report `session_path=relay`. Loopback-shim
counts and sidecar `RelayBytes` never supply coturn evidence.

`same-lan`/`cone`: `session_path=direct` and coturn 0. `relay`:
`session_path=relay` and coturn `rb=`/`sb=`. Dual-STUN.
`WEBJAM_TEST_HARNESS=1`, `WEBJAM_TEST_STUN_SERVER`; relay also
`WEBJAM_TEST_TURN_SERVER`, `WEBJAM_TEST_TURN_CREDENTIAL_URL`.
POST /turn-credentials. Deferred: pion vnet; hairpin. **NOT RUN**.
