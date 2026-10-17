# Cross-network acceptance (proof-only)

Three jobs must **PASS**. Peers drive `webjam-fabric` via `TransportProcess`
and `transport/internal/ipc` `direct-stun` (`directstun`, not
`reference-local`). Receipt `relay_bytes` is coturn `rb=`/`sb=` only.
Audio proof sends a fixed stream of 1,000 nonce-bound, numbered UDP frames at
2 ms intervals. Each frame is sent once. Both peers must verify at least 950
distinct frames; wrong payloads, duplicates, and out-of-range sequences fail.
This exercises live audio under the configured 1% loss per direction without
retransmitting audio or retrying a failed case. Receipts retain `music_frames`.
The lossless TURN integration test separately requires a single exact echo.
`same-lan`/`cone`: `session_path=direct` and coturn 0. `relay`:
`session_path=relay` and coturn `rb=`/`sb=`. Dual-STUN.
`WEBJAM_TEST_HARNESS=1`, `WEBJAM_TEST_STUN_SERVER`; relay also
`WEBJAM_TEST_TURN_SERVER`, `WEBJAM_TEST_TURN_CREDENTIAL_URL`.
POST /turn-credentials. Deferred: pion vnet; hairpin. **NOT RUN**.
