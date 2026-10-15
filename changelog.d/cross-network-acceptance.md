- Optional Cross-network acceptance: same-LAN PASS; two-NAT honest FAIL until
  Internet Join. `--expected-status FAIL` exits 0 only for a finished
  network-unreachable connect. `probe-nat` fails closed. Cone/masquerade: RFC 4787
  endpoint-independent mapping + address-and-port-dependent filtering.
