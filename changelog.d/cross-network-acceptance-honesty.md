- Tightened the optional Cross-network acceptance harness so guest crashes,
  timeouts, missing results, and symmetric fake connects cannot exit 0 under
  `--expected-status FAIL`; cone NETMAP now targets the real guest address;
  discarded TEST-NET-3 STUN mappings are not treated as cone-vs-symmetric
  product proof; and a later FAIL removes any stale `.PASS` for that SHA/case.
