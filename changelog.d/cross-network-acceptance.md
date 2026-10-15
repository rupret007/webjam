- Optional Cross-network acceptance harness: same-LAN must PASS; two-NAT
  stays an honest FAIL until Internet Join. Guest exit 1 after `guest.result`,
  crashes, timeouts, missing results, programming/startup failures, host
  crashes after ready/reap, connected-without-state, and fake connects cannot
  exit 0 under `--expected-status FAIL`. Cone/masquerade: RFC 4787
  endpoint-independent mapping + address-and-port-dependent filtering.
  Privileged CI runs `probe-nat` fail-closed. Not a required check.
