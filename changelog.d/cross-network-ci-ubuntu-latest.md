- Fixed the optional Cross-network acceptance workflow on ubuntu-latest:
  netns probe, sch_netem via linux-modules-extra, coturn in the public ns,
  runner-owned receipts for artifact upload, and `--expected-status FAIL` so
  the honest two-NAT FAIL does not redden CI. Same-LAN still must PASS.
  Cross-network stays report-only on master; networking PRs must match the
  expected receipt. Assertions are unchanged.
