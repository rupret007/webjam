- Mailbox quota also buckets IPv6 by /48. New address buckets are
  capped per IPv4 /24 and per IPv6 /32, not globally, so a rotator
  cannot lock out Host/Join. Known prefixes and per-net daily
  shares last the UTC day with no eviction and are stored as one
  SQLite row each, so a /32 adding about 32 /48s a minute cannot
  grow a shared quota value until the Durable Object 503s, and a
  new network is still served after hundreds of others. Unreadable
  quota counters fail closed instead of resetting the day. Several
  hosts behind one NAT get about 30 mailbox requests per minute;
  each prefix has a daily share of about 200, and each /24 or /32
  has a daily share of about 2000. About 50 prefixes in different
  nets can still use the 10k global cap. Failed-upgrade prefix
  inserts are capped at about 2000 per UTC day. The global daily
  caps stay the same. A WebSocket wait that fails immediately is
  not charged against the daily cap, but is still rate-limited.
  A successful mailbox reply that only carried an unused Upgrade
  header is still charged. POST or DELETE that only carry an
  Upgrade header must still pass the daily cap before they can
  mutate. Failed upgrades may remember a prefix for the
  new-bucket check, but those writes do not rewrite known
  prefixes. Each rate-only persist, plus daily prefix/net upserts,
  the quota KV put, and the UTC-day DELETE, counts against the
  daily row-write cap.
