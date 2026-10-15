/**
 * Tiny Cloudflare Worker: mint short-lived TURN iceServers.
 * TURN_KEY_ID and TURN_KEY_API_TOKEN stay in Worker secrets. They never
 * ship in the WebJam client.
 */

import { Mailbox, validMailboxId } from "./mailbox.js";
import {
  FETCH_TIMEOUT_MS,
  MAX_BODY_BYTES,
  USAGE_CACHE_MS,
  analyticsBearerToken,
  clientIP,
  credentialTtlSeconds,
  decideCap,
  fetchWithTimeout,
  iceServersDocument,
  missingRequiredSecrets,
  monthlyCapBytes,
  readUsageBytes,
  usageAnalyticsQuery,
  utcMonthDateRange,
  validSessionInviteShape,
} from "./policy.js";
import { MailboxQuota, applyQuota, emptyQuotaState, quotaLimits } from "./quota.js";

const GENERATE_URL =
  "https://rtc.live.cloudflare.com/v1/turn/keys/{key}/credentials/generate-ice-servers";
const GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql";
const SESSION_QUOTA_BYTES = 256 * 1024 * 1024;

let quotaState = emptyQuotaState();
let usageCache = null;

export function resetWorkerState() {
  quotaState = emptyQuotaState();
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

function parseMailboxPath(path) {
  const parts = String(path || "").split("/").filter(Boolean);
  if (parts[0] !== "mailbox" || parts.length < 2 || parts.length > 3) {
    return null;
  }
  if (!validMailboxId(parts[1])) {
    return { invalid: true };
  }
  return { id: parts[1], slot: parts[2] || "" };
}

function applyQuotaEnv(env) {
  const quotaEnv = {
    MAILBOX_RATE_LIMIT_PER_MINUTE:
      env.MAILBOX_RATE_LIMIT_PER_MINUTE ?? env.TURN_RATE_LIMIT_PER_MINUTE,
    MAILBOX_DAILY_REQUESTS: env.MAILBOX_DAILY_REQUESTS,
    MAILBOX_DAILY_ROW_WRITES: env.MAILBOX_DAILY_ROW_WRITES,
    MAILBOX_DAILY_GB_S: env.MAILBOX_DAILY_GB_S,
  };
  globalThis.WEBJAM_QUOTA_ENV = quotaEnv;
  return quotaEnv;
}

function requireHttps(request) {
  const url = new URL(request.url);
  if (url.protocol === "https:") {
    return null;
  }
  if (url.hostname === "127.0.0.1" || url.hostname === "localhost") {
    return null;
  }
  return errorResponse(403, "https");
}

async function enforceAccess(request, env, deps, nowMs, rowWrites) {
  if (typeof deps.enforceAccess === "function") {
    return deps.enforceAccess(request, nowMs, rowWrites);
  }
  const ip = clientIP(request.headers);
  if (env.MAILBOX_QUOTA && typeof env.MAILBOX_QUOTA.idFromName === "function") {
    try {
      const stub = env.MAILBOX_QUOTA.get(env.MAILBOX_QUOTA.idFromName("quota"));
      const response = await stub.fetch(
        new Request("https://quota/check", {
          method: "POST",
          headers: {
            "CF-Connecting-IP": ip,
            "X-WebJam-Now": String(nowMs),
            "X-WebJam-Row-Writes": String(rowWrites),
          },
        }),
      );
      return response.status === 200 ? null : response;
    } catch {
      return errorResponse(503, "unavailable");
    }
  }
  const decision = applyQuota(quotaState, {
    ip,
    nowMs,
    rowWrites,
    limits: quotaLimits(applyQuotaEnv(env)),
  });
  if (decision.allow) {
    return null;
  }
  return errorResponse(decision.status, decision.code);
}

async function handleMailbox(request, env, deps, mailbox, nowMs) {
  if (mailbox.invalid) {
    return errorResponse(404, "not_found");
  }
  const writes =
    request.method === "POST" || request.method === "DELETE" ? 1 : 0;
  const denied = await enforceAccess(request, env, deps, nowMs, writes);
  if (denied) {
    return denied;
  }
  if (typeof deps.mailboxFetch === "function") {
    return deps.mailboxFetch(request, mailbox, nowMs);
  }
  if (!env.MAILBOX || typeof env.MAILBOX.idFromName !== "function") {
    return errorResponse(503, "misconfigured");
  }
  const headers = new Headers(request.headers);
  headers.set("X-WebJam-Now", String(nowMs));
  try {
    const stub = env.MAILBOX.get(env.MAILBOX.idFromName(mailbox.id));
    return await stub.fetch(new Request(request, { headers }));
  } catch {
    return errorResponse(503, "unavailable");
  }
}

export async function handleRequest(request, env, deps = {}) {
  const blocked = requireHttps(request);
  if (blocked) {
    return blocked;
  }
  applyQuotaEnv(env ?? {});
  const fetchImpl = deps.fetch ?? globalThis.fetch;
  const nowMs = Number(deps.nowMs ?? Date.now());
  const path = new URL(request.url).pathname;
  const mailbox = parseMailboxPath(path);
  if (mailbox) {
    return handleMailbox(request, env, deps, mailbox, nowMs);
  }
  if (request.method !== "POST") {
    return errorResponse(405, "method");
  }
  if (path !== "/turn-credentials") {
    return errorResponse(404, "not_found");
  }

  const denied = await enforceAccess(request, env, deps, nowMs, 0);
  if (denied) {
    return denied;
  }

  if (missingRequiredSecrets(env).length > 0) {
    return errorResponse(503, "misconfigured");
  }
  const keyId = String(env.TURN_KEY_ID ?? "").trim();
  const apiToken = String(env.TURN_KEY_API_TOKEN ?? "").trim();
  const accountId = String(env.CLOUDFLARE_ACCOUNT_ID ?? "").trim();
  const timeoutMs = Number(deps.fetchTimeoutMs ?? FETCH_TIMEOUT_MS);

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
    accountId,
    nowMs,
    timeoutMs,
  );
  const decision = decideCap(usageBytes, capBytes);
  if (!decision.allow) {
    return errorResponse(decision.code === "cap" ? 429 : 503, decision.code);
  }

  const ttl = credentialTtlSeconds(env.TURN_CREDENTIAL_TTL_SECONDS);
  let generated;
  try {
    generated = await generateIceServers(
      fetchImpl,
      keyId,
      apiToken,
      ttl,
      timeoutMs,
    );
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
  accountId,
  nowMs,
  timeoutMs = FETCH_TIMEOUT_MS,
) {
  const range = utcMonthDateRange(new Date(nowMs));
  if (
    usageCache &&
    usageCache.monthKey === range.monthKey &&
    nowMs - usageCache.fetchedAt < USAGE_CACHE_MS
  ) {
    return usageCache.bytes;
  }
  const analyticsToken = analyticsBearerToken(env);
  if (!analyticsToken) {
    return null;
  }
  const payload = usageAnalyticsQuery(
    accountId,
    keyId,
    range.dateFrom,
    range.dateTo,
  );
  let response;
  try {
    response = await fetchWithTimeout(
      fetchImpl,
      GRAPHQL_URL,
      {
        method: "POST",
        headers: {
          authorization: `Bearer ${analyticsToken}`,
          "content-type": "application/json",
          accept: "application/json",
        },
        body: JSON.stringify(payload),
      },
      timeoutMs,
    );
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

async function generateIceServers(
  fetchImpl,
  keyId,
  apiToken,
  ttl,
  timeoutMs = FETCH_TIMEOUT_MS,
) {
  const url = GENERATE_URL.replace("{key}", encodeURIComponent(keyId));
  const response = await fetchWithTimeout(
    fetchImpl,
    url,
    {
      method: "POST",
      headers: {
        authorization: `Bearer ${apiToken}`,
        "content-type": "application/json",
        accept: "application/json",
      },
      body: JSON.stringify({ ttl }),
    },
    timeoutMs,
  );
  if (response.status !== 201 && response.status !== 200) {
    throw new Error("generate");
  }
  return response.json();
}

export { Mailbox, MailboxQuota };

export default {
  async fetch(request, env) {
    return handleRequest(request, env);
  },
};
