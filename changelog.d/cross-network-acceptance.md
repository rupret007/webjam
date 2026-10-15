- Added an optional Cross-network acceptance harness and GitHub workflow:
  same-LAN Host/Join must PASS; two-NAT stays an honest FAIL until Internet
  Join ships. Guest crashes (including after `guest.result`), timeouts,
  missing results, never-started processes, programming/startup failures,
  and fake connects cannot exit 0 under `--expected-status FAIL`. Relative
  sidecar `--output` resolves from the current directory. Cone NETMAP uses
  the real LAN endpoint; discarded TEST-NET-3 mappings are not NAT-type
  proof; a later FAIL removes a stale `.PASS`. Ubuntu CI installs netns,
  sch_netem, and coturn. Not a required check.
