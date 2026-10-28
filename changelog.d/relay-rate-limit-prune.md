- TURN credential rate limits (Cloudflare Worker and the Python relay
  helper) prune stale per-IP buckets so the store cannot grow without
  bound when many clients hit the same minute window.
