- Ordinary Host/Join keeps ICE-lite on the direct path and falls back to
  pion/turn relay candidates only when that pair fails. Coturn (Oracle
  Always Free Ampere A1) and Cloudflare Realtime TURN share one short-lived
  credential JSON. The long-lived secret stays off the shipped binary.
  Logs record `session_path` and `relay_bytes`. A compiled 256 MiB session
  cap plus the credential daily budget stop usage from billing. The two-NAT
  harness runs coturn in the public namespace: cone PASS direct, symmetric
  PASS relay. Musician setup is unchanged.
