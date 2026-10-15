/**
 * Shared Durable Object quota: daily counters plus per-IP rate.
 * Stored in one SQLite-backed DO so limits are not per Worker isolate.
 *
 * Caps sit well below the Workers/DO free plan (100k requests/day,
 * 13k GB-s, 100k row writes). Unreadable or missing counters fail closed.
 *
 * TURN and mailbox keep separate counters so a host wait cannot 429
 * credential minting. IPv6 is bucketed by /64 and /48. Per-minute rate
 * keys evict LRU at 256; known prefixes last the UTC day with no
 * eviction so a rotator cannot lock out a client already seen.
 *
 * Prefix records live in SQLite (one row per prefix, UTC-day expiry).
 * Each request reads and writes that one row instead of growing a
 * single quota:${scope} blob. An IPv6 /32 adding ~32 /48s a minute
 * therefore cannot push the Durable Object over the value-size or CPU
 * limit and 503 every user.
 *
 * There is no global new-prefix cap. New prefixes are capped per IPv4
 * /24 and per IPv6 /32. Each prefix has a daily share of 200. Each
 * /24 or /32 has a daily share of 2000, so one net cannot drain the
 * 10k global cap. About 50 prefixes in different nets can still use
 * the full 10k (50 x 200). Rate-only charges (failed upgrades) may
 * record a prefix for the new-bucket check, but inserts are capped
 * per UTC day (~2k) and already-known prefixes are not rewritten.
 * Each rate-only allow still rewrites ipBuckets and counts as one
 * daily row write so rotating failed upgrades cannot persist
 * unboundedly.
 */

export const DEFAULT_DAILY_REQUESTS = 10_000;
export const DEFAULT_DAILY_ROW_WRITES = 10_000;
export const DEFAULT_DAILY_GB_S = 1_000;
export const ESTIMATED_GB_S_PER_REQUEST = 0.01;
export const DEFAULT_RATE_LIMIT = 10;
export const DEFAULT_MAILBOX_RATE_LIMIT = 30;
export const DEFAULT_NEW_BUCKETS_PER_MINUTE = 32;
export const DEFAULT_PREFIX_DAILY_REQUESTS = 200;
export const DEFAULT_PREFIX_DAILY_ROW_WRITES = 200;
export const DEFAULT_PREFIX_DAILY_GB_S = 20;
export const DEFAULT_NET_DAILY_REQUESTS = 2_000;
export const DEFAULT_NET_DAILY_ROW_WRITES = 2_000;
export const DEFAULT_NET_DAILY_GB_S = 200;
export const DEFAULT_RATE_PREFIX_DAILY = 2_000;
export const RATE_WINDOW_MS = 60_000;
export const MAX_IP_BUCKETS = 256;
export const MAX_NET_SHARES = 512;
export const MAX_QUOTA_VALUE_BYTES = 96 * 1024;

export const PREFIX_SCHEMA_SQL =
  "CREATE TABLE IF NOT EXISTS known_prefixes (scope TEXT NOT NULL, prefix TEXT NOT NULL, day TEXT NOT NULL, at INTEGER NOT NULL, requests REAL NOT NULL DEFAULT 0, row_writes INTEGER NOT NULL DEFAULT 0, gb_s REAL NOT NULL DEFAULT 0, PRIMARY KEY (scope, prefix))";
export const SELECT_PREFIX_SQL =
  "SELECT at, requests, row_writes, gb_s FROM known_prefixes WHERE scope = ? AND prefix = ? AND day = ?";
export const UPSERT_PREFIX_SQL =
  "INSERT OR REPLACE INTO known_prefixes (scope, prefix, day, at, requests, row_writes, gb_s) VALUES (?, ?, ?, ?, ?, ?, ?)";
export const EXPIRE_PREFIX_SQL =
  "DELETE FROM known_prefixes WHERE scope = ? AND day != ?";

export function utcDayKey(nowMs) {
  return new Date(nowMs).toISOString().slice(0, 10);
}

export function quotaLimits(env = {}, scope = "mailbox") {
  const rateFallback =
    scope === "turn" ? DEFAULT_RATE_LIMIT : DEFAULT_MAILBOX_RATE_LIMIT;
  const rateVar =
    scope === "turn" ? "TURN_RATE_LIMIT_PER_MINUTE" : "MAILBOX_RATE_LIMIT_PER_MINUTE";
  const dailyRequests = positiveInt(env.MAILBOX_DAILY_REQUESTS, DEFAULT_DAILY_REQUESTS);
  const dailyRowWrites = positiveInt(
    env.MAILBOX_DAILY_ROW_WRITES,
    DEFAULT_DAILY_ROW_WRITES,
  );
  const dailyGbS = positiveInt(env.MAILBOX_DAILY_GB_S, DEFAULT_DAILY_GB_S);
  return {
    dailyRequests,
    dailyRowWrites,
    dailyGbS,
    perMinute: positiveInt(env[rateVar], rateFallback),
    newBucketsPerMinute: positiveInt(
      env.MAILBOX_NEW_BUCKETS_PER_MINUTE,
      DEFAULT_NEW_BUCKETS_PER_MINUTE,
    ),
    prefixDailyRequests: atMost(
      positiveInt(env.MAILBOX_PREFIX_DAILY_REQUESTS, DEFAULT_PREFIX_DAILY_REQUESTS),
      dailyRequests,
    ),
    prefixDailyRowWrites: atMost(
      positiveInt(env.MAILBOX_PREFIX_DAILY_ROW_WRITES, DEFAULT_PREFIX_DAILY_ROW_WRITES),
      dailyRowWrites,
    ),
    prefixDailyGbS: atMost(
      positiveInt(env.MAILBOX_PREFIX_DAILY_GB_S, DEFAULT_PREFIX_DAILY_GB_S),
      dailyGbS,
    ),
    netDailyRequests: atMost(
      positiveInt(env.MAILBOX_NET_DAILY_REQUESTS, DEFAULT_NET_DAILY_REQUESTS),
      dailyRequests,
    ),
    netDailyRowWrites: atMost(
      positiveInt(env.MAILBOX_NET_DAILY_ROW_WRITES, DEFAULT_NET_DAILY_ROW_WRITES),
      dailyRowWrites,
    ),
    netDailyGbS: atMost(
      positiveInt(env.MAILBOX_NET_DAILY_GB_S, DEFAULT_NET_DAILY_GB_S),
      dailyGbS,
    ),
    ratePrefixDaily: atMost(
      positiveInt(env.MAILBOX_RATE_PREFIX_DAILY, DEFAULT_RATE_PREFIX_DAILY),
      dailyRequests,
    ),
  };
}

function atMost(value, ceiling) {
  return value > ceiling ? ceiling : value;
}

function positiveInt(raw, fallback) {
  const parsed = Number.parseInt(String(raw ?? ""), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export function emptyQuotaState() {
  return {
    day: "",
    requests: 0,
    rowWrites: 0,
    gbS: 0,
    ipBuckets: {},
    newBuckets: {},
    knownPrefixes: {},
    netShares: {},
    ratePrefixWrites: 0,
  };
}

export function compactQuotaState(state) {
  return {
    day: state.day,
    requests: state.requests,
    rowWrites: state.rowWrites,
    gbS: state.gbS,
    ipBuckets: state.ipBuckets,
    newBuckets: state.newBuckets,
    netShares: state.netShares,
    ratePrefixWrites: state.ratePrefixWrites,
  };
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

export function prefix48(ip) {
  const key = rateKey(ip);
  if (!key.includes("/64")) {
    return key;
  }
  return `${ipv6Groups(ip).slice(0, 3).join(":")}::/48`;
}

export function prefixNet(ip) {
  const key = rateKey(ip);
  if (/^\d{1,3}(?:\.\d{1,3}){3}$/.test(key)) {
    const parts = key.split(".");
    return `${parts[0]}.${parts[1]}.${parts[2]}.0/24`;
  }
  if (key.includes("/64") || String(ip || "").includes(":")) {
    return `${ipv6Groups(ip).slice(0, 2).join(":")}::/32`;
  }
  return key;
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

export function applyQuota(state, { ip, nowMs, rowWrites = 0, limits, charge = "all" }) {
  if (state == null || typeof state !== "object") {
    return { allow: false, status: 503, code: "unreadable" };
  }
  if (!Number.isFinite(nowMs) || nowMs < 0 || limits == null) {
    return { allow: false, status: 503, code: "unreadable" };
  }
  const countRate = charge !== "daily";
  const countDaily = charge !== "rate";
  const day = utcDayKey(nowMs);
  if (state.day !== day) {
    state.day = day;
    state.requests = 0;
    state.rowWrites = 0;
    state.gbS = 0;
    state.ipBuckets = {};
    state.newBuckets = {};
    state.knownPrefixes = {};
    state.netShares = {};
    state.ratePrefixWrites = 0;
  }
  const key = rateKey(ip);
  if (!key) {
    return { allow: false, status: 429, code: "rate" };
  }
  const prefix = prefix48(ip);
  const net = prefixNet(ip);
  const window = Math.floor(nowMs / RATE_WINDOW_MS);
  pruneIpBuckets(state, window);
  pruneNewBuckets(state, window);
  if (state.knownPrefixes == null || typeof state.knownPrefixes !== "object") {
    state.knownPrefixes = {};
  }
  if (state.netShares == null || typeof state.netShares !== "object") {
    state.netShares = {};
  }
  const keys = prefix === key ? [key] : [key, prefix];
  if (countRate) {
    for (const nextKey of keys) {
      const ipKey = `${nextKey}:${window}`;
      const nextIp = (Number(state.ipBuckets[ipKey]) || 0) + 1;
      if (nextIp > limits.perMinute) {
        return { allow: false, status: 429, code: "rate" };
      }
    }
  }
  const isNew = !(prefix in state.knownPrefixes);
  const netKey = `${net}:${window}`;
  if (countRate && isNew) {
    const born = Number(state.newBuckets[netKey]) || 0;
    if (born >= limits.newBucketsPerMinute) {
      return { allow: false, status: 429, code: "rate" };
    }
  }
  const netKnown = Object.prototype.hasOwnProperty.call(state.netShares, net);
  const canTrackNet = netKnown || Object.keys(state.netShares).length < MAX_NET_SHARES;
  if (countDaily && isNew && !canTrackNet) {
    return { allow: false, status: 429, code: "rate" };
  }
  const writes = Number.isFinite(rowWrites) && rowWrites > 0 ? rowWrites : 0;
  let nextRequests = Number(state.requests) || 0;
  let nextWrites = Number(state.rowWrites) || 0;
  let nextGbS = Number(state.gbS) || 0;
  const prior = state.knownPrefixes[prefix];
  let prefixRequests = Number(prior && prior.requests) || 0;
  let prefixWrites = Number(prior && prior.rowWrites) || 0;
  let prefixGbS = Number(prior && prior.gbS) || 0;
  const netPrior = state.netShares[net];
  let netRequests = Number(netPrior && netPrior.requests) || 0;
  let netWrites = Number(netPrior && netPrior.rowWrites) || 0;
  let netGbS = Number(netPrior && netPrior.gbS) || 0;
  if (countDaily) {
    nextRequests += 1;
    nextWrites += writes;
    nextGbS += ESTIMATED_GB_S_PER_REQUEST;
    prefixRequests += 1;
    prefixWrites += writes;
    prefixGbS += ESTIMATED_GB_S_PER_REQUEST;
    netRequests += 1;
    netWrites += writes;
    netGbS += ESTIMATED_GB_S_PER_REQUEST;
    if (
      nextRequests > limits.dailyRequests ||
      nextWrites > limits.dailyRowWrites ||
      nextGbS > limits.dailyGbS ||
      prefixRequests > (limits.prefixDailyRequests ?? DEFAULT_PREFIX_DAILY_REQUESTS) ||
      prefixWrites > (limits.prefixDailyRowWrites ?? DEFAULT_PREFIX_DAILY_ROW_WRITES) ||
      prefixGbS > (limits.prefixDailyGbS ?? DEFAULT_PREFIX_DAILY_GB_S) ||
      netRequests > (limits.netDailyRequests ?? DEFAULT_NET_DAILY_REQUESTS) ||
      netWrites > (limits.netDailyRowWrites ?? DEFAULT_NET_DAILY_ROW_WRITES) ||
      netGbS > (limits.netDailyGbS ?? DEFAULT_NET_DAILY_GB_S)
    ) {
      return { allow: false, status: 429, code: "cap" };
    }
  }
  const rateOnly = countRate && !countDaily;
  const ratePrefixDaily = limits.ratePrefixDaily ?? DEFAULT_RATE_PREFIX_DAILY;
  let nextRatePrefixWrites = Number(state.ratePrefixWrites) || 0;
  if (rateOnly && isNew) {
    nextRatePrefixWrites += 1;
    if (nextRatePrefixWrites > ratePrefixDaily) {
      return { allow: false, status: 429, code: "cap" };
    }
  }
  if (rateOnly) {
    nextWrites += 1;
    if (nextWrites > limits.dailyRowWrites) {
      return { allow: false, status: 429, code: "cap" };
    }
  }
  if (countRate && isNew) {
    state.newBuckets[netKey] = (Number(state.newBuckets[netKey]) || 0) + 1;
  }
  if (countRate) {
    for (const nextKey of keys) {
      const ipKey = `${nextKey}:${window}`;
      const nextIp = (Number(state.ipBuckets[ipKey]) || 0) + 1;
      rememberIpBucket(state.ipBuckets, ipKey, nextIp);
    }
  }
  if (countDaily) {
    const record = touchKnownPrefix(state.knownPrefixes, prefix, nowMs);
    state.requests = nextRequests;
    state.rowWrites = nextWrites;
    state.gbS = nextGbS;
    record.requests = prefixRequests;
    record.rowWrites = prefixWrites;
    record.gbS = prefixGbS;
    if (canTrackNet) {
      const share =
        netPrior != null && typeof netPrior === "object"
          ? netPrior
          : { requests: 0, rowWrites: 0, gbS: 0 };
      share.requests = netRequests;
      share.rowWrites = netWrites;
      share.gbS = netGbS;
      state.netShares[net] = share;
    }
  } else if (rateOnly) {
    if (isNew) {
      touchKnownPrefix(state.knownPrefixes, prefix, nowMs);
      state.ratePrefixWrites = nextRatePrefixWrites;
    }
    state.rowWrites = nextWrites;
  }
  return {
    allow: true,
    status: 200,
    code: "ok",
    prefixWritten: countDaily || (rateOnly && isNew),
  };
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

function pruneNewBuckets(state, window) {
  const born =
    state.newBuckets != null && typeof state.newBuckets === "object"
      ? state.newBuckets
      : {};
  state.newBuckets = born;
  const suffix = `:${window}`;
  for (const key of Object.keys(born)) {
    if (!key.endsWith(suffix)) {
      delete born[key];
    }
  }
}

function touchKnownPrefix(known, prefix, nowMs) {
  const prior = known[prefix];
  const record =
    prior != null && typeof prior === "object"
      ? prior
      : { at: nowMs, requests: 0, rowWrites: 0, gbS: 0 };
  record.at = nowMs;
  known[prefix] = record;
  return record;
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

function quotaSql(storage) {
  return storage != null && storage.sql != null && typeof storage.sql.exec === "function"
    ? storage.sql
    : null;
}

function ensurePrefixTable(sql) {
  sql.exec(PREFIX_SCHEMA_SQL);
}

function readPrefixRow(sql, scope, prefix, day) {
  for (const row of sql.exec(SELECT_PREFIX_SQL, scope, prefix, day)) {
    return {
      at: Number(row.at) || 0,
      requests: Number(row.requests) || 0,
      rowWrites: Number(row.row_writes) || 0,
      gbS: Number(row.gb_s) || 0,
    };
  }
  return null;
}

function writePrefixRow(sql, scope, prefix, day, record) {
  sql.exec(
    UPSERT_PREFIX_SQL,
    scope,
    prefix,
    day,
    Number(record.at) || 0,
    Number(record.requests) || 0,
    Number(record.rowWrites) || 0,
    Number(record.gbS) || 0,
  );
}

function expirePrefixRows(sql, scope, day) {
  sql.exec(EXPIRE_PREFIX_SQL, scope, day);
}

function deniedResponse(status, code) {
  return new Response(JSON.stringify({ error: code }), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
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
      const sql = quotaSql(this.ctx.storage);
      if (sql == null) {
        return deniedResponse(503, "unavailable");
      }
      const nowMs = Number(request.headers.get("X-WebJam-Now") ?? Date.now());
      const ip = String(request.headers.get("CF-Connecting-IP") ?? "").trim();
      const writes = Number(request.headers.get("X-WebJam-Row-Writes") ?? 0);
      const chargeRaw = String(request.headers.get("X-WebJam-Quota-Charge") ?? "all");
      const charge =
        chargeRaw === "rate" || chargeRaw === "daily" ? chargeRaw : "all";
      const scope =
        request.headers.get("X-WebJam-Quota-Scope") === "turn" ? "turn" : "mailbox";
      const storageKey = `quota:${scope}`;
      let stored;
      try {
        stored = await this.ctx.storage.get(storageKey);
      } catch {
        stored = null;
      }
      const state =
        stored && typeof stored === "object" ? { ...stored } : emptyQuotaState();
      delete state.knownPrefixes;
      state.knownPrefixes = {};
      const env = this.env ?? globalThis.WEBJAM_QUOTA_ENV ?? {};
      const limits = quotaLimits(env, scope);
      const day = utcDayKey(nowMs);
      const prefix = prefix48(ip);
      ensurePrefixTable(sql);
      if (state.day !== day) {
        expirePrefixRows(sql, scope, day);
      }
      const row = prefix ? readPrefixRow(sql, scope, prefix, day) : null;
      if (row) {
        state.knownPrefixes[prefix] = row;
      }
      const decision = applyQuota(state, {
        ip,
        nowMs,
        rowWrites: writes,
        limits,
        charge,
      });
      if (decision.allow) {
        if (charge === "rate" && (Number(state.rowWrites) || 0) > limits.dailyRowWrites) {
          return deniedResponse(429, "cap");
        }
        if (decision.prefixWritten) {
          const record = state.knownPrefixes[prefix];
          if (record) {
            writePrefixRow(sql, scope, prefix, state.day, record);
          }
        }
        const persisted = compactQuotaState(state);
        const encoded = JSON.stringify(persisted);
        if (encoded.length > MAX_QUOTA_VALUE_BYTES) {
          return deniedResponse(429, "cap");
        }
        await this.ctx.storage.put(storageKey, persisted);
      }
      return deniedResponse(decision.status, decision.code);
    } catch {
      return deniedResponse(503, "unavailable");
    }
  }
}
