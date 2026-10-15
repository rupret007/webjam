/**
 * Credential-service policy for Cloudflare Realtime TURN.
 *
 * Month-to-date usage (fail closed at the cap, or if usage cannot be read):
 *
 *   usage_bytes = sum(ingressBytes) + sum(egressBytes)
 *                 over the UTC calendar month [month_start, today]
 *                 filtered by keyId = TURN_KEY_ID
 *
 *   cap_bytes   = TURN_MONTHLY_CAP_GB * 1_000_000_000
 *                 (default 800; Cloudflare's free allowance is 1,000 GB)
 *
 *   refuse new credentials when usage_bytes >= cap_bytes
 *   or when the analytics query is missing, errors, or unparseable.
 *
 * 1 GB = 1,000,000,000 bytes (Cloudflare billing). 800 GB leaves 200 GB of
 * the 1,000 GB free tier for in-flight allocations and analytics lag.
 */

export const GIGABYTE_BYTES = 1_000_000_000;
export const DEFAULT_CAP_GB = 800;
export const DEFAULT_TTL_SECONDS = 3600;
export const DEFAULT_RATE_LIMIT = 10;
export const RATE_WINDOW_MS = 60_000;
export const USAGE_CACHE_MS = 60_000;
export const MAX_BODY_BYTES = 4096;
export const FREE_ALLOWANCE_GB = 1000;

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SESSION_HEX_RE = /^[0-9a-f]{64}$/;
const INVITE_TOKEN_RE = /^[A-Za-z0-9_-]{32,256}$/;
const IPV4_RE =
  /^(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)$/;

// GraphQL Account Analytics rejects TURN key tokens (Cloudflare code 10000).
// Prefer CLOUDFLARE_ANALYTICS_API_TOKEN; fall back to the TURN token if unset.
export function analyticsBearerToken(env) {
  const analytics = String(env?.CLOUDFLARE_ANALYTICS_API_TOKEN ?? "").trim();
  if (analytics) {
    return analytics;
  }
  return String(env?.TURN_KEY_API_TOKEN ?? "").trim();
}

export function monthlyCapBytes(capGbRaw) {
  const parsed = Number.parseInt(String(capGbRaw ?? ""), 10);
  const gigabytes =
    Number.isFinite(parsed) && parsed > 0 ? parsed : DEFAULT_CAP_GB;
  return gigabytes * GIGABYTE_BYTES;
}

export function credentialTtlSeconds(raw) {
  const parsed = Number.parseInt(String(raw ?? ""), 10);
  if (!Number.isFinite(parsed) || parsed < 60 || parsed > 86400) {
    return DEFAULT_TTL_SECONDS;
  }
  return parsed;
}

export function rateLimitPerMinute(raw) {
  const parsed = Number.parseInt(String(raw ?? ""), 10);
  if (!Number.isFinite(parsed) || parsed < 1 || parsed > 120) {
    return DEFAULT_RATE_LIMIT;
  }
  return parsed;
}

export function utcMonthDateRange(now) {
  const stamp = now instanceof Date ? now : new Date(now);
  const year = stamp.getUTCFullYear();
  const month = String(stamp.getUTCMonth() + 1).padStart(2, "0");
  const day = String(stamp.getUTCDate()).padStart(2, "0");
  return {
    dateFrom: `${year}-${month}-01`,
    dateTo: `${year}-${month}-${day}`,
    monthKey: `${year}-${month}`,
  };
}

export function validSessionInviteShape(body) {
  if (body == null || typeof body !== "object" || Array.isArray(body)) {
    return false;
  }
  const session = String(body.session_id ?? "");
  const token = String(body.invite_token ?? body.token ?? "");
  const sessionOk = UUID_RE.test(session) || SESSION_HEX_RE.test(session);
  return sessionOk && INVITE_TOKEN_RE.test(token);
}

export function clientIP(headers) {
  const cf = String(headers.get("CF-Connecting-IP") ?? "").trim();
  if (looksLikeIP(cf)) {
    return cf;
  }
  const forwarded = String(headers.get("X-Forwarded-For") ?? "")
    .split(",")[0]
    .trim();
  if (looksLikeIP(forwarded)) {
    return forwarded;
  }
  return "";
}

function looksLikeIP(value) {
  if (IPV4_RE.test(value)) {
    return true;
  }
  return value.includes(":") && value.length >= 2 && value.length <= 45;
}

export function allowRate(store, ip, nowMs, limit, windowMs = RATE_WINDOW_MS) {
  if (!ip) {
    return false;
  }
  const bucket = Math.floor(nowMs / windowMs);
  const key = `${ip}:${bucket}`;
  const next = (store.get(key) ?? 0) + 1;
  store.set(key, next);
  if (store.size > 4096) {
    for (const existing of store.keys()) {
      const parts = existing.split(":");
      const marked = Number(parts[parts.length - 1]);
      if (marked < bucket - 1) {
        store.delete(existing);
      }
    }
  }
  return next <= limit;
}

export function readUsageBytes(payload) {
  if (payload == null || typeof payload !== "object" || Array.isArray(payload)) {
    return null;
  }
  if (Array.isArray(payload.errors) && payload.errors.length > 0) {
    return null;
  }
  const accounts = payload.data?.viewer?.accounts;
  if (!Array.isArray(accounts) || accounts.length !== 1) {
    return null;
  }
  const groups = accounts[0]?.callsTurnUsageAdaptiveGroups;
  if (!Array.isArray(groups)) {
    return null;
  }
  if (groups.length === 0) {
    return 0;
  }
  let total = 0;
  for (const group of groups) {
    const ingress = group?.sum?.ingressBytes;
    const egress = group?.sum?.egressBytes;
    if (
      !Number.isFinite(ingress) ||
      !Number.isFinite(egress) ||
      ingress < 0 ||
      egress < 0
    ) {
      return null;
    }
    total += ingress + egress;
  }
  return total;
}

export function decideCap(usageBytes, capBytes) {
  if (
    usageBytes == null ||
    !Number.isFinite(usageBytes) ||
    usageBytes < 0 ||
    !Number.isFinite(capBytes) ||
    capBytes <= 0
  ) {
    return { allow: false, code: "unreadable" };
  }
  if (usageBytes >= capBytes) {
    return { allow: false, code: "cap" };
  }
  return { allow: true, code: "ok" };
}

export function usageAnalyticsQuery(accountId, keyId, dateFrom, dateTo) {
  return {
    query: `query WebJamTurnMonthUsage($accountId: String!, $keyId: String!, $dateFrom: Date!, $dateTo: Date!) {
  viewer {
    accounts(filter: { accountTag: $accountId }) {
      callsTurnUsageAdaptiveGroups(
        limit: 1
        filter: { date_geq: $dateFrom, date_leq: $dateTo, keyId: $keyId }
      ) {
        sum { ingressBytes egressBytes }
      }
    }
  }
}`,
    variables: { accountId, keyId, dateFrom, dateTo },
  };
}

export function iceServersDocument(payload, quotaBytes) {
  if (payload == null || typeof payload !== "object") {
    return null;
  }
  const servers = payload.iceServers;
  if (!Array.isArray(servers) || servers.length === 0) {
    return null;
  }
  return {
    iceServers: servers,
    ttl: Number(payload.ttl) || DEFAULT_TTL_SECONDS,
    quota_bytes: quotaBytes,
  };
}
