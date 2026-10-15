- Optional Cross-network acceptance is proof-only: same-LAN, two cone NATs,
  and two symmetric NATs plus POST `/turn-credentials` must PASS. Proof
  peers drive real `webjam-fabric` IPC. Receipt `relay_bytes` is coturn
  session `rb=`/`sb=` only; loopback-shim counts are not proof. Cone and
  relay stay FAIL until `direct-stun` Host/Join is on master. Dual-STUN
  mapping is verified. No expected-FAIL taxonomy.
