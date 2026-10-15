/**
 * Tiny Cloudflare Worker: mint short-lived TURN iceServers.
 * TURN_KEY_ID and TURN_KEY_API_TOKEN stay in Worker secrets. They never
 * ship in the WebJam client.
 */

import {
  MAX_BODY_BYTES,
  USAGE_CACHE_MS,
  allowRate,
  clientIP,
  credentialTtlSeconds,
  decideCap,
  iceServersDocument,
  monthlyCapBytes,
  rateLimitPerMinute,
  readUsageBytes,
  usageAnalyticsQuery,
  utcMonthDateRange,
  validSessionInviteShape,
} from "./policy.js";

const GENERATE_URL =
  "https://rtc.live.cloudflare.com/v1/turn/keys/{key}/credentials/generate-ice-servers";
const GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql";
const SESSION_QUOTA_BYTES = 256 * 1024 * 1024;

const rateStore = new Map();
let usageCache = null;

export function resetWorkerState() {
  rateStore.clear();
  usageCache = null;
}

function jsonResponse(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json",
      "cache-control": "no-store",
    },
  });
}

function errorResponse(status, code) {
  return jsonResponse(status, { error: code });
}

export async function handleRequest(request, env, deps = {}) {
  const fetchImpl = deps.fetch ?? globalThis.fetch;
  const nowMs = Number(deps.nowMs ?? Date.now());
  if (request.method !== "POST") {
    return errorResponse(405, "method");
  }
  const path = new URL(request.url).pathname;
  if (path !== "/turn-credentials") {
    return errorResponse(404, "not_found");
  }

  const keyId = String(env.TURN_KEY_ID ?? "").trim();
  const apiToken = String(env.TURN_KEY_API_TOKEN ?? "").trim();
  const accountId = String(env.CLOUDFLARE_ACCOUNT_ID ?? "").trim();
  if (!keyId || !apiToken || !accountId) {
    return errorResponse(503, "unavailable");
  }

  const ip = clientIP(request.headers);
  if (
    !allowRate(
      rateStore,
      ip,
      nowMs,
      rateLimitPerMinute(env.TURN_RATE_LIMIT_PER_MINUTE),
    )
  ) {
    return errorResponse(429, "rate");
  }

  let body;
  try {
    const raw = await request.arrayBuffer();
    if (raw.byteLength === 0 || raw.byteLength > MAX_BODY_BYTES) {
      return errorResponse(403, "unauthorized");
    }
    body = JSON.parse(new TextDecoder().decode(raw));
  } catch {
    return errorResponse(403, "unauthorized");
  }
  if (!validSessionInviteShape(body)) {
    return errorResponse(403, "unauthorized");
  }

  const capBytes = monthlyCapBytes(env.TURN_MONTHLY_CAP_GB);
  const usageBytes = await monthUsageBytes(
    fetchImpl,
    env,
    keyId,
    apiToken,
    accountId,
    nowMs,
  );
  const decision = decideCap(usageBytes, capBytes);
  if (!decision.allow) {
    return errorResponse(decision.code === "cap" ? 429 : 503, decision.code);
  }

  const ttl = credentialTtlSeconds(env.TURN_CREDENTIAL_TTL_SECONDS);
  let generated;
  try {
    generated = await generateIceServers(fetchImpl, keyId, apiToken, ttl);
  } catch {
    return errorResponse(503, "unavailable");
  }
  const document = iceServersDocument(generated, SESSION_QUOTA_BYTES);
  if (document == null) {
    return errorResponse(503, "unavailable");
  }
  return jsonResponse(200, document);
}

async function monthUsageBytes(
  fetchImpl,
  env,
  keyId,
  apiToken,
  accountId,
  nowMs,
) {
  const range = utcMonthDateRange(new Date(nowMs));
  if (
    usageCache &&
    usageCache.monthKey === range.monthKey &&
    nowMs - usageCache.fetchedAt < USAGE_CACHE_MS
  ) {
    return usageCache.bytes;
  }
  const payload = usageAnalyticsQuery(
    accountId,
    keyId,
    range.dateFrom,
    range.dateTo,
  );
  let response;
  try {
    response = await fetchImpl(GRAPHQL_URL, {
      method: "POST",
      headers: {
        authorization: `Bearer ${apiToken}`,
        "content-type": "application/json",
        accept: "application/json",
      },
      body: JSON.stringify(payload),
    });
  } catch {
    return null;
  }
  if (response.status !== 200) {
    return null;
  }
  let parsed;
  try {
    parsed = await response.json();
  } catch {
    return null;
  }
  const bytes = readUsageBytes(parsed);
  if (bytes == null) {
    return null;
  }
  usageCache = { monthKey: range.monthKey, bytes, fetchedAt: nowMs };
  return bytes;
}

async function generateIceServers(fetchImpl, keyId, apiToken, ttl) {
  const url = GENERATE_URL.replace("{key}", encodeURIComponent(keyId));
  const response = await fetchImpl(url, {
    method: "POST",
    headers: {
      authorization: `Bearer ${apiToken}`,
      "content-type": "application/json",
      accept: "application/json",
    },
    body: JSON.stringify({ ttl }),
  });
  if (response.status !== 201 && response.status !== 200) {
    throw new Error("generate");
  }
  return response.json();
}

export default {
  async fetch(request, env) {
    return handleRequest(request, env);
  },
};
