- Mailbox quota also buckets IPv6 by /48. New address buckets are
  capped per IPv4 /24 and per IPv6 /32, not globally, so a rotator
  cannot lock out Host/Join. Known prefixes last the UTC day with no
  eviction. Several hosts behind one NAT get about 30 mailbox requests
  per minute; each prefix has a daily share of about 200. The global
  daily caps stay the same. A WebSocket wait that fails immediately is
  not charged against the daily cap, but is still rate-limited.
