- TURN credential rate limits (Cloudflare Worker and the Python relay
  helper) prune stale per-IP buckets so the store cannot grow without
  bound when many clients hit the same minute window.
- The Worker's in-memory `allowRate` helper (`policy.js`) now fails closed
  when its store is full of live counters: it refuses new IPs instead of
  evicting current-window counts, so an IP that was already denied is never
  re-admitted. Note: the `/turn-credentials` mint path is rate limited by
  `applyQuota` (`quota.js`) through `enforceAccess`, not by `allowRate`.
