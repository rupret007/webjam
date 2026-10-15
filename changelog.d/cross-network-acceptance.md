- Added an optional Cross-network acceptance harness and GitHub workflow:
  same-LAN Host/Join must PASS; two-NAT stays an honest FAIL until Internet
  Join ships. Guest crashes (including after `guest.result`), timeouts,
  missing results, never-started processes, programming/startup failures,
  host crashes after ready/reap, transport-connected guests that fail state
  exchange, and fake connects cannot exit 0 under `--expected-status FAIL`.
  Sidecar `--output` is cwd-relative. Cone NETMAP uses the real LAN endpoint
  and requires an outbound mapping (unsolicited inbound UDP is dropped);
  discarded TEST-NET-3 mappings are not NAT-type proof; a later FAIL removes
  a stale `.PASS`. Not a required check.
