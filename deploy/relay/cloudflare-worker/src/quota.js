/**
 * Shared Durable Object quota: daily counters plus per-IP rate.
 * Stored in one SQLite-backed DO so limits are not per Worker isolate.
 *
 * Caps sit well below the Workers/DO free plan (100k requests/day,
 * 13k GB-s, 100k row writes). Unreadable or missing counters fail closed.
 *
 * TURN and mailbox keep separate counters so a host wait cannot 429
 * credential minting. IPv6 is bucketed by /64. New IPs evict the LRU
 * bucket instead of locking the Worker out.
 */

export const DEFAULT_DAILY_REQUESTS = 10_000;
export const DEFAULT_DAILY_ROW_WRITES = 10_000;
export const DEFAULT_DAILY_GB_S = 1_000;
export const ESTIMATED_GB_S_PER_REQUEST = 0.01;
export const DEFAULT_RATE_LIMIT = 10;
export const RATE_WINDOW_MS = 60_000;
export const MAX_IP_BUCKETS = 256;

export function utcDayKey(nowMs) {
  return new Date(nowMs).toISOString().slice(0, 10);
}

export function quotaLimits(env = {}, scope = "mailbox") {
  const rateVar =
    scope === "turn" ? "TURN_RATE_LIMIT_PER_MINUTE" : "MAILBOX_RATE_LIMIT_PER_MINUTE";
  return {
    dailyRequests: positiveInt(env.MAILBOX_DAILY_REQUESTS, DEFAULT_DAILY_REQUESTS),
    dailyRowWrites: positiveInt(env.MAILBOX_DAILY_ROW_WRITES, DEFAULT_DAILY_ROW_WRITES),
    dailyGbS: positiveInt(env.MAILBOX_DAILY_GB_S, DEFAULT_DAILY_GB_S),
    perMinute: positiveInt(env[rateVar], DEFAULT_RATE_LIMIT),
  };
}

function positiveInt(raw, fallback) {
  const parsed = Number.parseInt(String(raw ?? ""), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export function emptyQuotaState() {
  return { day: "", requests: 0, rowWrites: 0, gbS: 0, ipBuckets: {} };
}

export function quotaScope(path) {
  return String(path || "").startsWith("/mailbox") ? "mailbox" : "turn";
}

export function rateKey(ip) {
  const value = String(ip || "").trim().toLowerCase();
  if (!value) {
    return "";
  }
  const mapped = value.match(/^(?:[:a-f0-9]*:)?ffff:(\d{1,3}(?:\.\d{1,3}){3})$/);
  if (mapped) {
    return mapped[1];
  }
  if (/^\d{1,3}(?:\.\d{1,3}){3}$/.test(value)) {
    return value;
  }
  if (value.includes(":")) {
    return `${ipv6Groups(value).slice(0, 4).join(":")}::/64`;
  }
  return value;
}

function ipv6Groups(ip) {
  const bare = String(ip || "").split("%")[0];
  const [left, right = ""] = bare.split("::");
  const leftParts = left ? left.split(":").filter(Boolean) : [];
  const rightParts = right
    ? right.split(":").filter((part) => part && !part.includes("."))
    : [];
  const missing = Math.max(0, 8 - leftParts.length - rightParts.length);
  const filled = [...leftParts, ...Array.from({ length: missing }, () => "0"), ...rightParts];
  while (filled.length < 8) {
    filled.push("0");
  }
  return filled.slice(0, 8).map((part) => {
    const hex = String(part || "0").replace(/^0+/, "");
    return hex || "0";
  });
}

export function applyQuota(state, { ip, nowMs, rowWrites = 0, limits }) {
  if (state == null || typeof state !== "object") {
    return { allow: false, status: 503, code: "unreadable" };
  }
  if (!Number.isFinite(nowMs) || nowMs < 0 || limits == null) {
    return { allow: false, status: 503, code: "unreadable" };
  }
  const day = utcDayKey(nowMs);
  if (state.day !== day) {
    state.day = day;
    state.requests = 0;
    state.rowWrites = 0;
    state.gbS = 0;
    state.ipBuckets = {};
  }
  const key = rateKey(ip);
  if (!key) {
    return { allow: false, status: 429, code: "rate" };
  }
  const window = Math.floor(nowMs / RATE_WINDOW_MS);
  pruneIpBuckets(state, window);
  const ipKey = `${key}:${window}`;
  const nextIp = (Number(state.ipBuckets[ipKey]) || 0) + 1;
  if (nextIp > limits.perMinute) {
    return { allow: false, status: 429, code: "rate" };
  }
  const writes = Number.isFinite(rowWrites) && rowWrites > 0 ? rowWrites : 0;
  const nextRequests = (Number(state.requests) || 0) + 1;
  const nextWrites = (Number(state.rowWrites) || 0) + writes;
  const nextGbS = (Number(state.gbS) || 0) + ESTIMATED_GB_S_PER_REQUEST;
  if (
    nextRequests > limits.dailyRequests ||
    nextWrites > limits.dailyRowWrites ||
    nextGbS > limits.dailyGbS
  ) {
    return { allow: false, status: 429, code: "cap" };
  }
  rememberIpBucket(state.ipBuckets, ipKey, nextIp);
  state.requests = nextRequests;
  state.rowWrites = nextWrites;
  state.gbS = nextGbS;
  return { allow: true, status: 200, code: "ok" };
}

function pruneIpBuckets(state, window) {
  const buckets =
    state.ipBuckets != null && typeof state.ipBuckets === "object"
      ? state.ipBuckets
      : {};
  state.ipBuckets = buckets;
  const suffix = `:${window}`;
  for (const key of Object.keys(buckets)) {
    if (!key.endsWith(suffix)) {
      delete buckets[key];
    }
  }
}

function rememberIpBucket(buckets, ipKey, count) {
  if (ipKey in buckets) {
    delete buckets[ipKey];
  } else {
    while (Object.keys(buckets).length >= MAX_IP_BUCKETS) {
      delete buckets[Object.keys(buckets)[0]];
    }
  }
  buckets[ipKey] = count;
}

export class MailboxQuota {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
    if (env != null && typeof env === "object") {
      globalThis.WEBJAM_QUOTA_ENV = env;
    }
  }

  async fetch(request) {
    try {
      const nowMs = Number(request.headers.get("X-WebJam-Now") ?? Date.now());
      const ip = String(request.headers.get("CF-Connecting-IP") ?? "").trim();
      const writes = Number(request.headers.get("X-WebJam-Row-Writes") ?? 0);
      const scope =
        request.headers.get("X-WebJam-Quota-Scope") === "turn" ? "turn" : "mailbox";
      const storageKey = `quota:${scope}`;
      const stored = await this.ctx.storage.get(storageKey);
      const state = stored && typeof stored === "object" ? stored : emptyQuotaState();
      const env = this.env ?? globalThis.WEBJAM_QUOTA_ENV ?? {};
      const decision = applyQuota(state, {
        ip,
        nowMs,
        rowWrites: writes,
        limits: quotaLimits(env, scope),
      });
      if (decision.allow) {
        await this.ctx.storage.put(storageKey, state);
      }
      return new Response(JSON.stringify({ error: decision.code }), {
        status: decision.status,
        headers: { "content-type": "application/json", "cache-control": "no-store" },
      });
    } catch {
      return new Response(JSON.stringify({ error: "unavailable" }), {
        status: 503,
        headers: { "content-type": "application/json", "cache-control": "no-store" },
      });
    }
  }
}
