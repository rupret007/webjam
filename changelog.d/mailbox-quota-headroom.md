- Mailbox quota also buckets IPv6 by /48 and caps new address buckets
  per minute, so rotating addresses cannot burn the daily cap. Several
  hosts behind one NAT get about 30 mailbox requests per minute; the
  global daily caps stay the same. A WebSocket wait that fails
  immediately is not charged.
