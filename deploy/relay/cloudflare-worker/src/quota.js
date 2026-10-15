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
 * Prefix and /24|/32 share records live in SQLite (one row each,
 * UTC-day expiry). Each request reads and writes those rows instead
 * of growing a single quota:${scope} blob. An IPv6 /32 adding ~32
 * /48s a minute therefore cannot push the Durable Object over the
 * value-size or CPU limit and 503 every user. There is no fixed
 * network table: a new /24 or /32 is still served after hundreds of
 * other nets.
 *
 * Unreadable quota counters fail closed: a thrown storage get must
 * not reset the day and overwrite the real counters.
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
 * unboundedly. Daily allows also count the prefix upsert, net
 * upsert, KV put, and UTC-day DELETE toward dailyRowWrites.
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
export const MAX_QUOTA_VALUE_BYTES = 96 * 1024;

export const PREFIX_SCHEMA_SQL =
  "CREATE TABLE IF NOT EXISTS known_prefixes (scope TEXT NOT NULL, prefix TEXT NOT NULL, day TEXT NOT NULL, at INTEGER NOT NULL, requests REAL NOT NULL DEFAULT 0, row_writes INTEGER NOT NULL DEFAULT 0, gb_s REAL NOT NULL DEFAULT 0, PRIMARY KEY (scope, prefix))";
export const SELECT_PREFIX_SQL =
  "SELECT at, requests, row_writes, gb_s FROM known_prefixes WHERE scope = ? AND prefix = ? AND day = ?";
export const UPSERT_PREFIX_SQL =
  "INSERT OR REPLACE INTO known_prefixes (scope, prefix, day, at, requests, row_writes, gb_s) VALUES (?, ?, ?, ?, ?, ?, ?)";
export const EXPIRE_PREFIX_SQL =
  "DELETE FROM known_prefixes WHERE scope = ? AND day != ?";
export const NET_SCHEMA_SQL =
  "CREATE TABLE IF NOT EXISTS net_shares (scope TEXT NOT NULL, net TEXT NOT NULL, day TEXT NOT NULL, requests REAL NOT NULL DEFAULT 0, row_writes INTEGER NOT NULL DEFAULT 0, gb_s REAL NOT NULL DEFAULT 0, PRIMARY KEY (scope, net))";
export const SELECT_NET_SQL =
  "SELECT requests, row_writes, gb_s FROM net_shares WHERE scope = ? AND net = ? AND day = ?";
export const UPSERT_NET_SQL =
  "INSERT OR REPLACE INTO net_shares (scope, net, day, requests, row_writes, gb_s) VALUES (?, ?, ?, ?, ?, ?)";
export const EXPIRE_NET_SQL =
  "DELETE FROM net_shares WHERE scope = ? AND day != ?";

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
    const share =
      netPrior != null && typeof netPrior === "object"
        ? netPrior
        : { requests: 0, rowWrites: 0, gbS: 0 };
    share.requests = netRequests;
    share.rowWrites = netWrites;
    share.gbS = netGbS;
    state.netShares[net] = share;
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
    netWritten: countDaily,
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

function ensureQuotaTables(sql) {
  sql.exec(PREFIX_SCHEMA_SQL);
  sql.exec(NET_SCHEMA_SQL);
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

function readNetRow(sql, scope, net, day) {
  for (const row of sql.exec(SELECT_NET_SQL, scope, net, day)) {
    return {
      requests: Number(row.requests) || 0,
      rowWrites: Number(row.row_writes) || 0,
      gbS: Number(row.gb_s) || 0,
    };
  }
  return null;
}

function writeNetRow(sql, scope, net, day, record) {
  sql.exec(
    UPSERT_NET_SQL,
    scope,
    net,
    day,
    Number(record.requests) || 0,
    Number(record.rowWrites) || 0,
    Number(record.gbS) || 0,
  );
}

function expireNetRows(sql, scope, day) {
  sql.exec(EXPIRE_NET_SQL, scope, day);
}

function extraPersistWrites({ charge, prefixWritten, netWritten, expiredTables }) {
  let extra = 0;
  const rateOnly = charge === "rate";
  if (!rateOnly) {
    extra += 1;
    if (prefixWritten) {
      extra += 1;
    }
  }
  if (netWritten) {
    extra += 1;
  }
  extra += Number(expiredTables) || 0;
  return extra;
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
      const stored = await this.ctx.storage.get(storageKey);
      let state;
      if (stored === undefined) {
        state = emptyQuotaState();
      } else if (stored != null && typeof stored === "object") {
        state = { ...stored };
      } else {
        return deniedResponse(503, "unreadable");
      }
      delete state.knownPrefixes;
      delete state.netShares;
      state.knownPrefixes = {};
      state.netShares = {};
      const env = this.env ?? globalThis.WEBJAM_QUOTA_ENV ?? {};
      const limits = quotaLimits(env, scope);
      const day = utcDayKey(nowMs);
      const prefix = prefix48(ip);
      const net = prefixNet(ip);
      ensureQuotaTables(sql);
      const hadPriorDay = Boolean(state.day) && state.day !== day;
      if (state.day !== day) {
        expirePrefixRows(sql, scope, day);
        expireNetRows(sql, scope, day);
      }
      const prefixRow = prefix ? readPrefixRow(sql, scope, prefix, day) : null;
      if (prefixRow) {
        state.knownPrefixes[prefix] = prefixRow;
      }
      const netRow = net ? readNetRow(sql, scope, net, day) : null;
      if (netRow) {
        state.netShares[net] = netRow;
      }
      const decision = applyQuota(state, {
        ip,
        nowMs,
        rowWrites: writes,
        limits,
        charge,
      });
      if (decision.allow) {
        const extra = extraPersistWrites({
          charge,
          prefixWritten: decision.prefixWritten,
          netWritten: decision.netWritten,
          expiredTables: hadPriorDay ? 2 : 0,
        });
        const nextWrites = (Number(state.rowWrites) || 0) + extra;
        if (nextWrites > limits.dailyRowWrites) {
          return deniedResponse(429, "cap");
        }
        state.rowWrites = nextWrites;
        const persisted = compactQuotaState(state);
        const encoded = JSON.stringify(persisted);
        if (encoded.length > MAX_QUOTA_VALUE_BYTES) {
          return deniedResponse(429, "cap");
        }
        if (decision.prefixWritten) {
          const record = state.knownPrefixes[prefix];
          if (record) {
            writePrefixRow(sql, scope, prefix, state.day, record);
          }
        }
        if (decision.netWritten) {
          const share = state.netShares[net];
          if (share) {
            writeNetRow(sql, scope, net, state.day, share);
          }
        }
        await this.ctx.storage.put(storageKey, persisted);
      }
      return deniedResponse(decision.status, decision.code);
    } catch {
      return deniedResponse(503, "unavailable");
    }
  }
}
