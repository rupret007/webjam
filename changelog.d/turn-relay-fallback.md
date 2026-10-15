- Ordinary Host/Join keeps ICE-lite on the direct path and falls back to
  pion/turn relay candidates only when that pair fails. Cloudflare Realtime
  TURN (`webjam-relay`) is minted by a free-plan Worker at
  `POST /turn-credentials`; coturn stays a switchback on the same JSON.
  The key ID and API token are Worker secrets, never the shipped binary.
  The Worker refuses new credentials at 800 GB of the 1,000 GB free
  month (ingress+egress) or if usage cannot be read. Logs record
  `session_path` and `relay_bytes`. A compiled 256 MiB session cap still
  applies. Musician setup is unchanged.
