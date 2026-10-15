- Optional Cross-network acceptance harness: same-LAN must PASS; two-NAT
  stays an honest FAIL until Internet Join. Only a finished
  network-unreachable connect can exit 0 under `--expected-status FAIL`.
  `probe-nat` fails on nonzero subprocesses or missing mappings.
  Cone/masquerade: RFC 4787 endpoint-independent mapping +
  address-and-port-dependent filtering.
