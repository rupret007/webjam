- Added an optional Cross-network acceptance harness and GitHub workflow:
  same-LAN Host/Join must PASS; two-NAT stays an honest FAIL until Internet
  Join ships. Guest crashes, timeouts, missing results, and symmetric fake
  connects cannot exit 0 under `--expected-status FAIL`. Cone NETMAP uses the
  real LAN endpoint; discarded TEST-NET-3 STUN mappings are not product
  NAT-type proof; a later FAIL removes a stale `.PASS`. Ubuntu CI installs
  netns, sch_netem, and coturn, chowns receipts for upload, and can drive
  packaged direct-stun via a test-only netns STUN mapping. Not a required check.
