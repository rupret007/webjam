import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_CAP_GB,
  DEFAULT_TTL_SECONDS,
  FETCH_TIMEOUT_MS,
  FREE_ALLOWANCE_GB,
  UPSTREAM_FETCHES_PER_REQUEST,
  WORKER_WORST_CASE_MS,
  GIGABYTE_BYTES,
  allowRate,
  analyticsBearerToken,
  decideCap,
  fetchWithTimeout,
  iceServersDocument,
  missingRequiredSecrets,
  monthlyCapBytes,
  readUsageBytes,
  utcMonthDateRange,
  validSessionInviteShape,
} from "../src/policy.js";
import { handleRequest, resetWorkerState } from "../src/worker.js";

const INVITE = "A".repeat(43);
const SESSION_UUID = "6ba7b810-9dad-11d1-80b4-00c04fd430c8";
const SESSION_HEX = "ab".repeat(32);

function env() {
  return {
    TURN_KEY_ID: "webjam-relay-key-id",
    TURN_KEY_API_TOKEN: "test-turn-api-token",
    CLOUDFLARE_ACCOUNT_ID: "test-account",
    CLOUDFLARE_ANALYTICS_API_TOKEN: "test-analytics-token",
    TURN_MONTHLY_CAP_GB: "800",
    TURN_CREDENTIAL_TTL_SECONDS: "600",
    TURN_RATE_LIMIT_PER_MINUTE: "10",
  };
}

function icePayload() {
  return {
    iceServers: [
      { urls: ["stun:stun.cloudflare.com:3478"] },
      {
        urls: ["turn:turn.cloudflare.com:3478?transport=udp"],
        username: "cf-user",
        credential: "cf-pass",
      },
    ],
  };
}

function usagePayload(ingress, egress) {
  return {
    data: {
      viewer: {
        accounts: [
          {
            callsTurnUsageAdaptiveGroups: [
              { sum: { ingressBytes: ingress, egressBytes: egress } },
            ],
          },
        ],
      },
    },
  };
}

function post(body, headers = { "CF-Connecting-IP": "203.0.113.10" }) {
  return new Request("https://worker.test/turn-credentials", {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
}

function fakeFetch(usage, generate = icePayload(), generateStatus = 201) {
  const seen = { graphql: "", generate: "" };
  const fetchImpl = async (url, init) => {
    const target = String(url);
    const bearer = String(init?.headers?.authorization ?? "");
    if (target.includes("graphql")) {
      seen.graphql = bearer;
      if (usage instanceof Error) {
        throw usage;
      }
      if (usage && usage.status) {
        return new Response(usage.body ?? "no", { status: usage.status });
      }
      return new Response(JSON.stringify(usage), { status: 200 });
    }
    if (target.includes("generate-ice-servers")) {
      seen.generate = bearer;
      assert.match(target, /\/turn\/keys\/webjam-relay-key-id\/credentials\/generate-ice-servers$/);
      assert.equal(init.method, "POST");
      assert.match(bearer, /^Bearer /);
      const sent = JSON.parse(init.body);
      assert.equal(sent.ttl, DEFAULT_TTL_SECONDS);
      assert.equal(sent.ttl, 600);
      return new Response(JSON.stringify(generate), { status: generateStatus });
    }
    return new Response("unexpected", { status: 500 });
  };
  fetchImpl.seen = seen;
  return fetchImpl;
}

test("cap formula uses 800 of 1000 decimal GB and refuses at the bound", () => {
  const cap = monthlyCapBytes(DEFAULT_CAP_GB);
  assert.equal(cap, 800 * GIGABYTE_BYTES);
  assert.ok(cap < FREE_ALLOWANCE_GB * GIGABYTE_BYTES);
  assert.deepEqual(decideCap(0, cap), { allow: true, code: "ok" });
  assert.deepEqual(decideCap(cap - 1, cap), { allow: true, code: "ok" });
  assert.deepEqual(decideCap(cap, cap), { allow: false, code: "cap" });
  assert.deepEqual(decideCap(cap + 1, cap), { allow: false, code: "cap" });
  assert.deepEqual(decideCap(null, cap), { allow: false, code: "unreadable" });
  assert.deepEqual(decideCap(undefined, cap), { allow: false, code: "unreadable" });
  assert.deepEqual(decideCap(Number.NaN, cap), { allow: false, code: "unreadable" });
  assert.deepEqual(decideCap(-1, cap), { allow: false, code: "unreadable" });
  assert.equal(monthlyCapBytes("nope"), 800 * GIGABYTE_BYTES);
});

test("usage bytes are ingress plus egress and fail closed on bad analytics", () => {
  assert.equal(readUsageBytes(usagePayload(10, 20)), 30);
  assert.equal(readUsageBytes({
    data: { viewer: { accounts: [{ callsTurnUsageAdaptiveGroups: [] }] } },
  }), 0);
  assert.equal(readUsageBytes({ errors: [{ message: "denied" }] }), null);
  assert.equal(readUsageBytes({ data: { viewer: { accounts: [] } } }), null);
  assert.equal(readUsageBytes(usagePayload(10, undefined)), null);
  assert.equal(readUsageBytes(null), null);
});

test("session and invite token shapes match WebJam credentials", () => {
  assert.equal(
    validSessionInviteShape({ session_id: SESSION_UUID, invite_token: INVITE }),
    true,
  );
  assert.equal(
    validSessionInviteShape({ session_id: SESSION_HEX, invite_token: INVITE }),
    true,
  );
  assert.equal(
    validSessionInviteShape({ session_id: SESSION_UUID, token: INVITE }),
    true,
  );
  assert.equal(validSessionInviteShape({ session_id: SESSION_UUID }), false);
  assert.equal(
    validSessionInviteShape({ session_id: "not-a-uuid", invite_token: INVITE }),
    false,
  );
  assert.equal(
    validSessionInviteShape({ session_id: SESSION_UUID, invite_token: "short" }),
    false,
  );
  assert.equal(validSessionInviteShape({}), false);
});

test("rate limiter is per IP and fail-closed without an IP", () => {
  const store = new Map();
  for (let i = 0; i < 10; i += 1) {
    assert.equal(allowRate(store, "203.0.113.10", 1_000, 10), true);
  }
  assert.equal(allowRate(store, "203.0.113.10", 1_000, 10), false);
  assert.equal(allowRate(store, "203.0.113.11", 1_000, 10), true);
  assert.equal(allowRate(store, "", 1_000, 10), false);
});

test("UTC month range is the first of the month through today", () => {
  const range = utcMonthDateRange(new Date("2026-10-15T13:00:00Z"));
  assert.deepEqual(range, {
    dateFrom: "2026-10-01",
    dateTo: "2026-10-15",
    monthKey: "2026-10",
  });
});

test("worker mints iceServers under the cap", async () => {
  resetWorkerState();
  const response = await handleRequest(
    post({ session_id: SESSION_UUID, invite_token: INVITE }),
    env(),
    { fetch: fakeFetch(usagePayload(1, 2)), nowMs: Date.parse("2026-10-15T00:00:00Z") },
  );
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.ok(Array.isArray(body.iceServers));
  assert.equal(body.iceServers[1].username, "cf-user");
  assert.equal(iceServersDocument(icePayload(), 1).quota_bytes, 1);
});

test("worker refuses at the monthly cap", async () => {
  resetWorkerState();
  const cap = 800 * GIGABYTE_BYTES;
  const response = await handleRequest(
    post({ session_id: SESSION_HEX, invite_token: INVITE }),
    env(),
    {
      fetch: fakeFetch(usagePayload(cap, 0)),
      nowMs: Date.parse("2026-10-15T00:00:00Z"),
    },
  );
  assert.equal(response.status, 429);
  assert.deepEqual(await response.json(), { error: "cap" });
});

test("worker fails closed when usage cannot be read", async () => {
  resetWorkerState();
  const denied = await handleRequest(
    post({ session_id: SESSION_UUID, invite_token: INVITE }),
    env(),
    {
      fetch: fakeFetch({ errors: [{ message: "no" }] }),
      nowMs: Date.parse("2026-10-15T00:00:00Z"),
    },
  );
  assert.equal(denied.status, 503);
  const down = await handleRequest(
    post({ session_id: SESSION_UUID, invite_token: INVITE }),
    env(),
    {
      fetch: fakeFetch({ status: 500, body: "no" }),
      nowMs: Date.parse("2026-10-15T00:00:00Z"),
    },
  );
  assert.equal(down.status, 503);
  const crashed = await handleRequest(
    post({ session_id: SESSION_UUID, invite_token: INVITE }),
    env(),
    {
      fetch: fakeFetch(new Error("network")),
      nowMs: Date.parse("2026-10-15T00:00:00Z"),
    },
  );
  assert.equal(crashed.status, 503);
});

test("analytics token is preferred for GraphQL and unused for iceServers", () => {
  assert.equal(
    analyticsBearerToken({
      TURN_KEY_API_TOKEN: "turn-token",
      CLOUDFLARE_ANALYTICS_API_TOKEN: "analytics-token",
    }),
    "analytics-token",
  );
  assert.equal(
    analyticsBearerToken({ TURN_KEY_API_TOKEN: "turn-token" }),
    "turn-token",
  );
  assert.equal(
    analyticsBearerToken({
      TURN_KEY_API_TOKEN: "turn-token",
      CLOUDFLARE_ANALYTICS_API_TOKEN: "   ",
    }),
    "turn-token",
  );
  assert.equal(analyticsBearerToken({}), "");
});

test("default credential TTL is 600 seconds", () => {
  assert.equal(DEFAULT_TTL_SECONDS, 600);
  assert.equal(FETCH_TIMEOUT_MS, 5000);
  assert.equal(UPSTREAM_FETCHES_PER_REQUEST, 2);
  assert.equal(WORKER_WORST_CASE_MS, 10_000);
});

test("outbound fetch timeout still applies while the body is read", async () => {
  const headersThenHang = async () => ({
    status: 200,
    statusText: "OK",
    headers: new Headers(),
    arrayBuffer: () => new Promise(() => {}),
  });
  await assert.rejects(() =>
    fetchWithTimeout(headersThenHang, "https://example.test", {}, 20),
  );
});

test("worker uses analytics secret only on GraphQL and TURN token on generate", async () => {
  resetWorkerState();
  const fetchImpl = fakeFetch(usagePayload(1, 2));
  const response = await handleRequest(
    post({ session_id: SESSION_UUID, invite_token: INVITE }),
    { ...env(), CLOUDFLARE_ANALYTICS_API_TOKEN: "analytics-account-token" },
    { fetch: fetchImpl, nowMs: Date.parse("2026-10-15T00:00:00Z") },
  );
  assert.equal(response.status, 200);
  assert.equal(fetchImpl.seen.graphql, "Bearer analytics-account-token");
  assert.equal(fetchImpl.seen.generate, "Bearer test-turn-api-token");
});

test("worker GraphQL uses analytics secret from the required four", async () => {
  resetWorkerState();
  const fetchImpl = fakeFetch(usagePayload(1, 2));
  const response = await handleRequest(
    post({ session_id: SESSION_UUID, invite_token: INVITE }),
    env(),
    { fetch: fetchImpl, nowMs: Date.parse("2026-10-15T00:00:00Z") },
  );
  assert.equal(response.status, 200);
  assert.equal(fetchImpl.seen.graphql, "Bearer test-analytics-token");
  assert.equal(fetchImpl.seen.generate, "Bearer test-turn-api-token");
});

test("TURN key token rejected by GraphQL still fails closed", async () => {
  resetWorkerState();
  const denied = await handleRequest(
    post({ session_id: SESSION_UUID, invite_token: INVITE }),
    env(),
    {
      fetch: fakeFetch({
        errors: [{ code: 10000, message: "Authentication error" }],
      }),
      nowMs: Date.parse("2026-10-15T00:00:00Z"),
    },
  );
  assert.equal(denied.status, 503);
  assert.deepEqual(await denied.json(), { error: "unreadable" });
});

test("missing any of the four required secrets returns 503 misconfigured", async () => {
  assert.deepEqual(missingRequiredSecrets(env()), []);
  for (const name of [
    "TURN_KEY_ID",
    "TURN_KEY_API_TOKEN",
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_ANALYTICS_API_TOKEN",
  ]) {
    resetWorkerState();
    const broken = { ...env(), [name]: "  " };
    assert.deepEqual(missingRequiredSecrets(broken), [name]);
    const response = await handleRequest(
      post({ session_id: SESSION_UUID, invite_token: INVITE }),
      broken,
      { fetch: fakeFetch(usagePayload(0, 0)), nowMs: 1 },
    );
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { error: "misconfigured" });
  }
});

test("outbound fetch timeout fails closed", async () => {
  const hung = async (_url, init) =>
    new Promise((_, reject) => {
      init.signal.addEventListener("abort", () => {
        reject(init.signal.reason ?? new Error("aborted"));
      });
    });
  await assert.rejects(() => fetchWithTimeout(hung, "https://example.test", {}, 20));

  resetWorkerState();
  const timedOut = await handleRequest(
    post({ session_id: SESSION_UUID, invite_token: INVITE }),
    env(),
    {
      fetch: hung,
      nowMs: Date.parse("2026-10-15T00:00:00Z"),
      fetchTimeoutMs: 20,
    },
  );
  assert.equal(timedOut.status, 503);
  assert.deepEqual(await timedOut.json(), { error: "unreadable" });
});

test("worker rejects bad token shape and missing secrets", async () => {
  resetWorkerState();
  const bad = await handleRequest(
    post({ session_id: "x", invite_token: "y" }),
    env(),
    { fetch: fakeFetch(usagePayload(0, 0)), nowMs: 1 },
  );
  assert.equal(bad.status, 403);
  const missing = await handleRequest(
    post({ session_id: SESSION_UUID, invite_token: INVITE }),
    { ...env(), TURN_KEY_API_TOKEN: "" },
    { fetch: fakeFetch(usagePayload(0, 0)), nowMs: 1 },
  );
  assert.equal(missing.status, 503);
  assert.deepEqual(await missing.json(), { error: "misconfigured" });
  const get = await handleRequest(
    new Request("https://worker.test/turn-credentials"),
    env(),
  );
  assert.equal(get.status, 405);
});
