- Mailbox quota admits every Durable Object storage write or delete
  before it executes. Rate-only persists spend the same prefix and
  net write shares as mailbox ops, prefix inserts count as row
  writes, and expiry deletes charge the deleted row count rather
  than a table. A mailbox put pre-charges the later TTL delete.
  The daily row-write cap is never exceeded.
