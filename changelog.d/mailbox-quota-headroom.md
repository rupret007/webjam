- Mailbox quota also buckets IPv6 by /48 and caps new address buckets
  per minute, so rotating addresses cannot burn the daily cap. Known
  prefixes last the UTC day, so that cap cannot lock out clients already
  seen. Several hosts behind one NAT get about 30 mailbox requests per
  minute; each prefix has a daily share, and the global daily caps stay
  the same. A WebSocket wait that fails immediately is not charged
  against the daily cap, but is still rate-limited.
