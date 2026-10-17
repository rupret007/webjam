import assert from "node:assert/strict";
import test from "node:test";

import {
  applyMailbox,
  emptyMailbox,
  MAX_PAYLOAD_BYTES,
  MAX_WAITERS,
  validContentLength,
  validMailboxId,
  validPayload,
  waiterAccepted,
} from "../src/mailbox.js";
import {
  applyQuota,
  compactQuotaState,
  DEFAULT_DAILY_GB_S,
  DEFAULT_DAILY_REQUESTS,
  DEFAULT_DAILY_ROW_WRITES,
  DEFAULT_MAILBOX_RATE_LIMIT,
  DEFAULT_NET_DAILY_GB_S,
  DEFAULT_NET_DAILY_REQUESTS,
  DEFAULT_NET_DAILY_ROW_WRITES,
  DEFAULT_NEW_BUCKETS_PER_MINUTE,
  DEFAULT_PREFIX_DAILY_REQUESTS,
  DEFAULT_RATE_LIMIT,
  DEFAULT_RATE_PREFIX_DAILY,
  emptyQuotaState,
  ESTIMATED_GB_S_PER_REQUEST,
  COUNT_EXPIRED_NET_SQL,
  COUNT_EXPIRED_PREFIX_SQL,
  EXPIRE_NET_SQL,
  EXPIRE_PREFIX_SQL,
  MailboxQuota,
  MAX_IP_BUCKETS,
  MAX_QUOTA_VALUE_BYTES,
  NET_SCHEMA_SQL,
  PREFIX_SCHEMA_SQL,
  prefix48,
  prefixNet,
  quotaLimits,
  RATE_WINDOW_MS,
  rateKey,
  SELECT_NET_SQL,
  SELECT_PREFIX_SQL,
  UPSERT_NET_SQL,
  UPSERT_PREFIX_SQL,
} from "../src/quota.js";
import { memoryQuotaStorage, quotaCheck } from "./quota-memory.js";
import { handleRequest, resetWorkerState } from "../src/worker.js";

const ID = "ab".repeat(32);
const PAYLOAD = "dGVzdC1jaXBoZXJ0ZXh0";
const LIMITS = quotaLimits({});

function memoryMailbox() {
  const rooms = new Map();
  return async (request, mailbox, nowMs) => {
    const url = new URL(request.url);
    const slot = url.pathname.split("/").filter(Boolean).pop();
    let action;
    if (request.method === "DELETE") {
      action = { kind: "delete" };
    } else if (request.method === "POST") {
      const body = await request.json();
      action = { kind: "put", slot, payload: String(body.payload ?? "") };
    } else {
      action = { kind: "get", slot };
    }
    const state = rooms.get(mailbox.id) ?? emptyMailbox();
    const result = applyMailbox(state, action, nowMs);
    if (result.clear) {
      rooms.delete(mailbox.id);
    } else if (result.status === 201) {
      rooms.set(mailbox.id, state);
    }
    if (result.status === 204) {
      return new Response(null, { status: 204 });
    }
    return new Response(
      JSON.stringify(result.payload ? { payload: result.payload } : { error: result.code }),
      { status: result.status, headers: { "content-type": "application/json" } },
    );
  };
}

function mailboxRequest(method, slot, body, headers = { "CF-Connecting-IP": "203.0.113.10" }) {
  const init = { method, headers: { "content-type": "application/json", ...headers } };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
  }
  return new Request(`https://worker.test/mailbox/${ID}/${slot}`, init);
}

test("mailbox id is a 32-byte hex locator and never a raw capability", () => {
  assert.equal(validMailboxId(ID), true);
  assert.equal(validMailboxId("A".repeat(43)), false);
  assert.equal(validMailboxId(""), false);
  assert.equal(validPayload(PAYLOAD), true);
  assert.equal(validPayload("x".repeat(MAX_PAYLOAD_BYTES + 1)), false);
});

test("offer and answer are write-once and a second answer is rejected", () => {
  const state = emptyMailbox();
  const now = 1_000;
  assert.equal(applyMailbox(state, { kind: "put", slot: "offer", payload: PAYLOAD }, now).status, 201);
  assert.equal(applyMailbox(state, { kind: "put", slot: "offer", payload: PAYLOAD }, now).status, 409);
  assert.equal(applyMailbox(state, { kind: "put", slot: "answer", payload: PAYLOAD }, now).status, 201);
  const second = applyMailbox(state, { kind: "put", slot: "answer", payload: "b3RoZXI" }, now);
  assert.equal(second.status, 409);
  assert.equal(second.code, "duplicate");
});

test("expired or lost mailbox is gone", () => {
  const state = emptyMailbox();
  applyMailbox(state, { kind: "put", slot: "offer", payload: PAYLOAD }, 1_000);
  const expired = applyMailbox(state, { kind: "get", slot: "offer" }, 1_000 + 600_001);
  assert.equal(expired.status, 410);
  assert.equal(expired.code, "expired");
  assert.equal(expired.clear, true);
  const fresh = emptyMailbox();
  assert.equal(applyMailbox(fresh, { kind: "get", slot: "answer" }, 1_000).status, 204);
  fresh.consumed = true;
  assert.equal(applyMailbox(fresh, { kind: "get", slot: "answer" }, 1_000).status, 410);
});

test("host delete after a valid read clears the mailbox", () => {
  const state = emptyMailbox();
  applyMailbox(state, { kind: "put", slot: "answer", payload: PAYLOAD }, 5);
  const got = applyMailbox(state, { kind: "get", slot: "answer" }, 6);
  assert.equal(got.status, 200);
  assert.equal(got.payload, PAYLOAD);
  const gone = applyMailbox(state, { kind: "delete" }, 7);
  assert.equal(gone.status, 204);
  assert.equal(gone.clear, true);
});

test("delete without an answer does not ack or clear", () => {
  const state = emptyMailbox();
  applyMailbox(state, { kind: "put", slot: "offer", payload: PAYLOAD }, 5);
  const missing = applyMailbox(state, { kind: "delete" }, 6);
  assert.equal(missing.status, 404);
  assert.notEqual(missing.clear, true);
  assert.equal(state.offer, PAYLOAD);
});

test("daily counters keep at least 4x headroom under the free plan", () => {
  assert.ok(DEFAULT_DAILY_REQUESTS * 4 <= 100_000);
  assert.ok(DEFAULT_DAILY_ROW_WRITES * 4 <= 100_000);
  assert.ok(DEFAULT_DAILY_GB_S * 4 <= 13_000);
  const state = emptyQuotaState();
  const tight = { ...LIMITS, dailyRequests: 1 };
  assert.equal(applyQuota(state, { ip: "203.0.113.10", nowMs: 1, limits: tight }).allow, true);
  const capped = applyQuota(state, { ip: "203.0.113.10", nowMs: 1, limits: tight });
  assert.equal(capped.allow, false);
  assert.equal(capped.status, 429);
  assert.equal(capped.code, "cap");
  assert.equal(applyQuota(null, { ip: "203.0.113.10", nowMs: 1, limits: LIMITS }).status, 503);
  assert.ok(ESTIMATED_GB_S_PER_REQUEST * DEFAULT_DAILY_REQUESTS < 13_000);
});

test("per-IP rate limit lives in durable quota state", () => {
  const state = emptyQuotaState();
  assert.equal(LIMITS.perMinute, DEFAULT_MAILBOX_RATE_LIMIT);
  assert.equal(DEFAULT_MAILBOX_RATE_LIMIT, 30);
  assert.equal(quotaLimits({}, "turn").perMinute, DEFAULT_RATE_LIMIT);
  assert.equal(LIMITS.newBucketsPerMinute, DEFAULT_NEW_BUCKETS_PER_MINUTE);
  assert.equal(DEFAULT_PREFIX_DAILY_REQUESTS, 200);
  assert.equal(LIMITS.prefixDailyRequests, 200);
  assert.equal(DEFAULT_RATE_PREFIX_DAILY, 2_000);
  assert.equal(LIMITS.ratePrefixDaily, 2_000);
  assert.equal(DEFAULT_NET_DAILY_REQUESTS, 2_000);
  assert.equal(DEFAULT_NET_DAILY_ROW_WRITES, 2_000);
  assert.equal(DEFAULT_NET_DAILY_GB_S, 200);
  assert.equal(LIMITS.netDailyRequests, 2_000);
  assert.equal(LIMITS.netDailyRowWrites, 2_000);
  assert.equal(LIMITS.netDailyGbS, 200);
  for (let i = 0; i < DEFAULT_MAILBOX_RATE_LIMIT; i += 1) {
    assert.equal(applyQuota(state, { ip: "203.0.113.10", nowMs: 1, limits: LIMITS }).allow, true);
  }
  assert.equal(applyQuota(state, { ip: "203.0.113.10", nowMs: 1, limits: LIMITS }).code, "rate");
  assert.equal(applyQuota(state, { ip: "203.0.113.11", nowMs: 1, limits: LIMITS }).allow, true);
  assert.equal(applyQuota(state, { ip: "", nowMs: 1, limits: LIMITS }).code, "rate");
});

test("ipBuckets from a prior window are pruned", () => {
  const state = emptyQuotaState();
  state.day = "1970-01-01";
  state.ipBuckets = { "203.0.113.9:99": 9 };
  assert.equal(applyQuota(state, { ip: "203.0.113.10", nowMs: 1, limits: LIMITS }).allow, true);
  assert.equal(state.ipBuckets["203.0.113.9:99"], undefined);
  assert.equal(state.ipBuckets["203.0.113.10:0"], 1);
  assert.ok(Object.keys(state.ipBuckets).length <= MAX_IP_BUCKETS);
});

test("IPv6 addresses in the same /64 share a rate bucket", () => {
  const state = emptyQuotaState();
  const tight = { ...LIMITS, perMinute: 2 };
  assert.equal(rateKey("2001:db8:1:2:aaaa::1"), "2001:db8:1:2::/64");
  assert.equal(prefix48("2001:db8:1:2:aaaa::1"), "2001:db8:1::/48");
  assert.equal(prefixNet("2001:db8:1:2:aaaa::1"), "2001:db8::/32");
  assert.equal(prefixNet("203.0.113.10"), "203.0.113.0/24");
  assert.equal(rateKey("::ffff:203.0.113.10"), "203.0.113.10");
  assert.equal(prefixNet("::ffff:203.0.113.10"), "203.0.113.0/24");
  assert.equal(applyQuota(state, { ip: "2001:db8:1:2:aaaa::1", nowMs: 1, limits: tight }).allow, true);
  assert.equal(applyQuota(state, { ip: "2001:db8:1:2:bbbb::2", nowMs: 1, limits: tight }).allow, true);
  assert.equal(applyQuota(state, { ip: "2001:db8:1:2:cccc::3", nowMs: 1, limits: tight }).code, "rate");
});

test("IPv6 addresses in the same /48 share a rate bucket", () => {
  const state = emptyQuotaState();
  const tight = { ...LIMITS, perMinute: 2 };
  assert.equal(applyQuota(state, { ip: "2001:db8:1:2::1", nowMs: 1, limits: tight }).allow, true);
  assert.equal(applyQuota(state, { ip: "2001:db8:1:3::2", nowMs: 1, limits: tight }).allow, true);
  assert.equal(applyQuota(state, { ip: "2001:db8:1:4::3", nowMs: 1, limits: tight }).code, "rate");
  assert.equal(applyQuota(state, { ip: "2001:db8:2::1", nowMs: 1, limits: tight }).allow, true);
});

test("a flood of new IPs does not block a legitimate user", () => {
  const state = emptyQuotaState();
  const roomy = { ...LIMITS, newBucketsPerMinute: 1_000 };
  const legit = "203.0.113.10";
  assert.equal(applyQuota(state, { ip: legit, nowMs: 1, limits: roomy }).allow, true);
  for (let i = 0; i < MAX_IP_BUCKETS + 64; i += 1) {
    const ip = `198.51.${(i >> 8) & 255}.${i & 255}`;
    assert.equal(applyQuota(state, { ip, nowMs: 1, limits: roomy }).allow, true);
  }
  assert.equal(applyQuota(state, { ip: legit, nowMs: 1, limits: roomy }).allow, true);
  assert.ok(Object.keys(state.ipBuckets).length <= MAX_IP_BUCKETS);
  assert.ok(legit in state.knownPrefixes);
});

test("a cap on new buckets stops rotating addresses from burning the daily cap", () => {
  const state = emptyQuotaState();
  const tight = { ...LIMITS, newBucketsPerMinute: 2, dailyRequests: 10_000 };
  assert.equal(applyQuota(state, { ip: "203.0.113.10", nowMs: 1, limits: tight }).allow, true);
  assert.equal(applyQuota(state, { ip: "203.0.113.11", nowMs: 1, limits: tight }).allow, true);
  assert.equal(applyQuota(state, { ip: "203.0.113.12", nowMs: 1, limits: tight }).code, "rate");
  assert.equal(applyQuota(state, { ip: "203.0.113.10", nowMs: 1, limits: tight }).allow, true);
  assert.equal(state.requests, 3);
  assert.equal(
    applyQuota(state, { ip: "2001:db8:1:2::1", nowMs: 1, limits: tight }).allow,
    true,
  );
  const rotator = emptyQuotaState();
  const oneNew = { ...LIMITS, newBucketsPerMinute: 1, perMinute: 30 };
  assert.equal(applyQuota(rotator, { ip: "2001:db8:9:1::1", nowMs: 1, limits: oneNew }).allow, true);
  assert.equal(applyQuota(rotator, { ip: "2001:db8:9:2::2", nowMs: 1, limits: oneNew }).allow, true);
  assert.equal(applyQuota(rotator, { ip: "2001:db8:a::1", nowMs: 1, limits: oneNew }).code, "rate");
  assert.equal(rotator.requests, 2);
});

test("a known prefix is still allowed after the new-bucket cap fills next minute", () => {
  for (const scope of ["mailbox", "turn"]) {
    const state = emptyQuotaState();
    const limits = { ...quotaLimits({}, scope), newBucketsPerMinute: 2 };
    assert.equal(applyQuota(state, { ip: "203.0.113.10", nowMs: 1, limits }).allow, true);
    assert.equal(applyQuota(state, { ip: "203.0.113.11", nowMs: 1, limits }).allow, true);
    const nextMinute = 1 + RATE_WINDOW_MS;
    assert.equal(applyQuota(state, { ip: "198.51.100.1", nowMs: nextMinute, limits }).allow, true);
    assert.equal(applyQuota(state, { ip: "198.51.100.2", nowMs: nextMinute, limits }).allow, true);
    assert.equal(applyQuota(state, { ip: "198.51.100.3", nowMs: nextMinute, limits }).code, "rate");
    assert.equal(applyQuota(state, { ip: "203.0.113.10", nowMs: nextMinute, limits }).allow, true);
    assert.equal(applyQuota(state, { ip: "203.0.113.11", nowMs: nextMinute, limits }).allow, true);
  }
});

test("an idle known prefix is still served after 300 rotating prefixes", () => {
  for (const scope of ["mailbox", "turn"]) {
    const state = emptyQuotaState();
    const limits = quotaLimits({}, scope);
    const legit = "203.0.113.10";
    const seenAt = 1;
    const later = seenAt + 10 * RATE_WINDOW_MS;
    assert.equal(applyQuota(state, { ip: legit, nowMs: seenAt, limits }).allow, true);
    let added = 0;
    for (let i = 0; i < 300; i += 1) {
      const ip = `198.${(i >> 8) & 255}.${i & 255}.10`;
      const result = applyQuota(state, { ip, nowMs: later, limits });
      if (result.allow) {
        added += 1;
      }
    }
    assert.ok(added >= 300);
    assert.ok(Object.keys(state.knownPrefixes).length >= 301);
    assert.equal(applyQuota(state, { ip: legit, nowMs: later, limits }).allow, true);
    assert.ok(legit in state.knownPrefixes);
  }
});

test("one prefix cannot drain the daily cap", () => {
  const state = emptyQuotaState();
  const tight = {
    ...LIMITS,
    dailyRequests: 100,
    prefixDailyRequests: 2,
  };
  assert.ok(DEFAULT_PREFIX_DAILY_REQUESTS <= DEFAULT_DAILY_REQUESTS);
  assert.ok(LIMITS.prefixDailyRequests <= LIMITS.dailyRequests);
  assert.equal(applyQuota(state, { ip: "203.0.113.10", nowMs: 1, limits: tight }).allow, true);
  assert.equal(applyQuota(state, { ip: "203.0.113.10", nowMs: 1, limits: tight }).allow, true);
  assert.equal(applyQuota(state, { ip: "203.0.113.10", nowMs: 1, limits: tight }).code, "cap");
  assert.equal(applyQuota(state, { ip: "203.0.113.11", nowMs: 1, limits: tight }).allow, true);
  assert.equal(state.requests, 3);
});

test("one /24 or /32 cannot drain the daily cap", () => {
  assert.ok(DEFAULT_NET_DAILY_REQUESTS <= DEFAULT_DAILY_REQUESTS);
  assert.ok(LIMITS.netDailyRequests <= LIMITS.dailyRequests);
  assert.ok(DEFAULT_PREFIX_DAILY_REQUESTS * 50 <= DEFAULT_DAILY_REQUESTS);
  const v4 = emptyQuotaState();
  const tight = {
    ...LIMITS,
    dailyRequests: 100,
    prefixDailyRequests: 50,
    netDailyRequests: 2,
  };
  assert.equal(applyQuota(v4, { ip: "203.0.113.10", nowMs: 1, limits: tight }).allow, true);
  assert.equal(applyQuota(v4, { ip: "203.0.113.11", nowMs: 1, limits: tight }).allow, true);
  assert.equal(applyQuota(v4, { ip: "203.0.113.12", nowMs: 1, limits: tight }).code, "cap");
  assert.equal(applyQuota(v4, { ip: "198.51.100.1", nowMs: 1, limits: tight }).allow, true);
  assert.equal(v4.requests, 3);
  const v6 = emptyQuotaState();
  assert.equal(applyQuota(v6, { ip: "2001:db8:1::1", nowMs: 1, limits: tight }).allow, true);
  assert.equal(applyQuota(v6, { ip: "2001:db8:2::1", nowMs: 1, limits: tight }).allow, true);
  assert.equal(applyQuota(v6, { ip: "2001:db8:3::1", nowMs: 1, limits: tight }).code, "cap");
  assert.equal(applyQuota(v6, { ip: "2001:db9::1", nowMs: 1, limits: tight }).allow, true);
  assert.equal(v6.requests, 3);
});

test("a 60s mailbox wait at the 10s poll interval does not 429", () => {
  const state = emptyQuotaState();
  let denied = 0;
  let allowed = 0;
  for (let nowMs = 0; nowMs <= 60_000; nowMs += 10_000) {
    const result = applyQuota(state, { ip: "203.0.113.10", nowMs, limits: LIMITS });
    if (result.allow) {
      allowed += 1;
    } else {
      denied += 1;
    }
  }
  assert.equal(denied, 0);
  assert.ok(allowed >= 6);
});

test("content-length is checked before the body is read", () => {
  assert.equal(validContentLength("12"), true);
  assert.equal(validContentLength("0"), false);
  assert.equal(validContentLength(String(MAX_PAYLOAD_BYTES + 65)), false);
  assert.equal(validContentLength(""), false);
});

test("websocket waiters are capped at two", () => {
  assert.equal(MAX_WAITERS, 2);
  assert.equal(waiterAccepted(0), true);
  assert.equal(waiterAccepted(1), true);
  assert.equal(waiterAccepted(2), false);
});

test("worker mailbox posts and reads through the replaceable route", async () => {
  resetWorkerState();
  const mailboxFetch = memoryMailbox();
  const posted = await handleRequest(
    mailboxRequest("POST", "answer", { payload: PAYLOAD }),
    { MAILBOX_DAILY_REQUESTS: "10000" },
    { mailboxFetch, nowMs: 10 },
  );
  assert.equal(posted.status, 201);
  const got = await handleRequest(
    mailboxRequest("GET", "answer"),
    {},
    { mailboxFetch, nowMs: 11 },
  );
  assert.equal(got.status, 200);
  assert.deepEqual(await got.json(), { payload: PAYLOAD });
  const missing = await handleRequest(
    new Request("https://worker.test/mailbox/not-a-hash/answer", { method: "GET" }),
    {},
    { mailboxFetch, nowMs: 12 },
  );
  assert.equal(missing.status, 404);
});

test("mailbox polling does not consume the TURN rate counter", async () => {
  resetWorkerState();
  const mailboxFetch = memoryMailbox();
  const nowMs = Date.parse("2026-10-15T00:00:00Z");
  const env = {
    TURN_KEY_ID: "webjam-relay-key-id",
    TURN_KEY_API_TOKEN: "test-turn-api-token",
    CLOUDFLARE_ACCOUNT_ID: "test-account",
    CLOUDFLARE_ANALYTICS_API_TOKEN: "test-analytics-token",
    TURN_MONTHLY_CAP_GB: "800",
    TURN_CREDENTIAL_TTL_SECONDS: "600",
    TURN_RATE_LIMIT_PER_MINUTE: "10",
    MAILBOX_RATE_LIMIT_PER_MINUTE: "10",
  };
  for (let i = 0; i < 10; i += 1) {
    const polled = await handleRequest(
      mailboxRequest("GET", "answer"),
      env,
      { mailboxFetch, nowMs },
    );
    assert.equal(polled.status, 204);
  }
  const blocked = await handleRequest(
    mailboxRequest("GET", "answer"),
    env,
    { mailboxFetch, nowMs },
  );
  assert.equal(blocked.status, 429);
  const minted = await handleRequest(
    new Request("https://worker.test/turn-credentials", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "CF-Connecting-IP": "203.0.113.10",
      },
      body: JSON.stringify({
        session_id: "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        invite_token: "A".repeat(43),
      }),
    }),
    env,
    {
      mailboxFetch,
      nowMs,
      fetch: async (url) => {
        if (String(url).includes("graphql")) {
          return new Response(
            JSON.stringify({
              data: {
                viewer: {
                  accounts: [
                    {
                      callsTurnUsageAdaptiveGroups: [
                        { sum: { ingressBytes: 1, egressBytes: 1 } },
                      ],
                    },
                  ],
                },
              },
            }),
            { status: 200 },
          );
        }
        return new Response(
          JSON.stringify({
            iceServers: [
              { urls: ["stun:stun.cloudflare.com:3478"] },
              {
                urls: ["turn:turn.cloudflare.com:3478?transport=udp"],
                username: "cf-user",
                credential: "cf-pass",
              },
            ],
          }),
          { status: 201 },
        );
      },
    },
  );
  assert.equal(minted.status, 200);
});

test("a websocket that fails immediately is not charged", async () => {
  resetWorkerState();
  const mailboxFetch = async (request) => {
    if (String(request.headers.get("Upgrade") || "").toLowerCase() === "websocket") {
      return new Response(JSON.stringify({ error: "rate" }), {
        status: 429,
        headers: { "content-type": "application/json" },
      });
    }
    return new Response(null, { status: 204 });
  };
  const env = { MAILBOX_DAILY_REQUESTS: "1" };
  const failed = await handleRequest(
    mailboxRequest("GET", "answer", undefined, {
      "CF-Connecting-IP": "203.0.113.10",
      Upgrade: "websocket",
    }),
    env,
    { mailboxFetch, nowMs: 1 },
  );
  assert.equal(failed.status, 429);
  const got = await handleRequest(
    mailboxRequest("GET", "answer"),
    env,
    { mailboxFetch, nowMs: 1 },
  );
  assert.equal(got.status, 204);
});

test("a websocket upgrade that succeeds is charged", async () => {
  resetWorkerState();
  const mailboxFetch = async (request) => {
    if (String(request.headers.get("Upgrade") || "").toLowerCase() === "websocket") {
      // Node's Response rejects 101; the Worker only reads `.status` here.
      return { status: 101 };
    }
    return new Response(null, { status: 204 });
  };
  const env = { MAILBOX_DAILY_REQUESTS: "1" };
  const accepted = await handleRequest(
    mailboxRequest("GET", "answer", undefined, {
      "CF-Connecting-IP": "203.0.113.10",
      Upgrade: "websocket",
    }),
    env,
    { mailboxFetch, nowMs: 1 },
  );
  assert.equal(accepted.status, 101);
  const blocked = await handleRequest(
    mailboxRequest("GET", "answer"),
    env,
    { mailboxFetch, nowMs: 1 },
  );
  assert.equal(blocked.status, 429);
  const body = await blocked.json();
  assert.equal(body.error, "cap");
});

test("a failed websocket upgrade is still rate limited before the mailbox", async () => {
  resetWorkerState();
  let forwarded = 0;
  const mailboxFetch = async () => {
    forwarded += 1;
    return new Response(JSON.stringify({ error: "rate" }), {
      status: 429,
      headers: { "content-type": "application/json" },
    });
  };
  const env = { MAILBOX_RATE_LIMIT_PER_MINUTE: "2" };
  const headers = { "CF-Connecting-IP": "203.0.113.10", Upgrade: "websocket" };
  const first = await handleRequest(
    mailboxRequest("GET", "answer", undefined, headers),
    env,
    { mailboxFetch, nowMs: 1 },
  );
  const second = await handleRequest(
    mailboxRequest("GET", "answer", undefined, headers),
    env,
    { mailboxFetch, nowMs: 1 },
  );
  const third = await handleRequest(
    mailboxRequest("GET", "answer", undefined, headers),
    env,
    { mailboxFetch, nowMs: 1 },
  );
  assert.equal(first.status, 429);
  assert.equal(second.status, 429);
  assert.equal(third.status, 429);
  assert.equal(forwarded, 2);
});

test("a denied charge after a 101 closes the accepted socket", async () => {
  resetWorkerState();
  let closed = null;
  const mailboxFetch = async (request) => {
    if (String(request.headers.get("Upgrade") || "").toLowerCase() === "websocket") {
      return {
        status: 101,
        webSocket: {
          close(code, reason) {
            closed = { code, reason };
          },
        },
      };
    }
    return new Response(null, { status: 204 });
  };
  const env = { MAILBOX_DAILY_REQUESTS: "1" };
  const emptied = await handleRequest(
    mailboxRequest("GET", "answer"),
    env,
    { mailboxFetch, nowMs: 1 },
  );
  assert.equal(emptied.status, 204);
  const denied = await handleRequest(
    mailboxRequest("GET", "answer", undefined, {
      "CF-Connecting-IP": "203.0.113.10",
      Upgrade: "websocket",
    }),
    env,
    { mailboxFetch, nowMs: 1 },
  );
  assert.equal(denied.status, 429);
  assert.equal(closed.code, 1013);
  assert.equal(closed.reason, "quota");
});

test("a successful non-101 mailbox operation with an Upgrade header is charged daily", async () => {
  resetWorkerState();
  const mailboxFetch = memoryMailbox();
  const env = { MAILBOX_DAILY_REQUESTS: "1" };
  const posted = await handleRequest(
    mailboxRequest("POST", "answer", { payload: PAYLOAD }, {
      "CF-Connecting-IP": "203.0.113.10",
      Upgrade: "websocket",
    }),
    env,
    { mailboxFetch, nowMs: 1 },
  );
  assert.equal(posted.status, 201);
  const blocked = await handleRequest(
    mailboxRequest("GET", "answer"),
    env,
    { mailboxFetch, nowMs: 1 },
  );
  assert.equal(blocked.status, 429);
  assert.equal((await blocked.json()).error, "cap");
});

test("Upgrade plus POST or DELETE cannot mutate after the daily cap is exhausted", async () => {
  resetWorkerState();
  let forwarded = 0;
  const inner = memoryMailbox();
  const mailboxFetch = async (request, mailbox, nowMs) => {
    forwarded += 1;
    return inner(request, mailbox, nowMs);
  };
  const env = { MAILBOX_DAILY_REQUESTS: "1" };
  const drained = await handleRequest(
    mailboxRequest("GET", "answer"),
    env,
    { mailboxFetch, nowMs: 1 },
  );
  assert.equal(drained.status, 204);
  assert.equal(forwarded, 1);

  const posted = await handleRequest(
    mailboxRequest("POST", "answer", { payload: PAYLOAD }, {
      "CF-Connecting-IP": "203.0.113.10",
      Upgrade: "websocket",
    }),
    env,
    { mailboxFetch, nowMs: 1 },
  );
  assert.equal(posted.status, 429);
  assert.equal((await posted.json()).error, "cap");
  assert.equal(forwarded, 1);

  resetWorkerState();
  const leaked = await handleRequest(
    mailboxRequest("GET", "answer"),
    env,
    { mailboxFetch, nowMs: 2 },
  );
  assert.equal(leaked.status, 204);

  const seeded = memoryMailbox();
  resetWorkerState();
  forwarded = 0;
  const seedFetch = async (request, mailbox, nowMs) => {
    forwarded += 1;
    return seeded(request, mailbox, nowMs);
  };
  const roomy = { MAILBOX_DAILY_REQUESTS: "2" };
  const stored = await handleRequest(
    mailboxRequest("POST", "answer", { payload: PAYLOAD }),
    roomy,
    { mailboxFetch: seedFetch, nowMs: 3 },
  );
  assert.equal(stored.status, 201);
  const filled = await handleRequest(
    mailboxRequest("GET", "answer"),
    roomy,
    { mailboxFetch: seedFetch, nowMs: 3 },
  );
  assert.equal(filled.status, 200);
  assert.equal(forwarded, 2);
  const deleted = await handleRequest(
    mailboxRequest("DELETE", "answer", undefined, {
      "CF-Connecting-IP": "203.0.113.10",
      Upgrade: "websocket",
    }),
    roomy,
    { mailboxFetch: seedFetch, nowMs: 3 },
  );
  assert.equal(deleted.status, 429);
  assert.equal((await deleted.json()).error, "cap");
  assert.equal(forwarded, 2);

  resetWorkerState();
  const remaining = await handleRequest(
    mailboxRequest("GET", "answer"),
    roomy,
    { mailboxFetch: seedFetch, nowMs: 4 },
  );
  assert.equal(remaining.status, 200);
  assert.deepEqual(await remaining.json(), { payload: PAYLOAD });
});

test("failed upgrades cannot grow or rewrite known-prefix state without a daily bound", () => {
  const state = emptyQuotaState();
  const limits = {
    ...LIMITS,
    perMinute: 100,
    newBucketsPerMinute: 100,
    ratePrefixDaily: 2,
  };
  const first = applyQuota(state, {
    ip: "203.0.113.10",
    nowMs: 1,
    limits,
    charge: "rate",
  });
  assert.equal(first.allow, true);
  const storedAt = state.knownPrefixes["203.0.113.10"].at;
  assert.equal(
    applyQuota(state, {
      ip: "203.0.113.11",
      nowMs: 2,
      limits,
      charge: "rate",
    }).allow,
    true,
  );
  assert.equal(Object.keys(state.knownPrefixes).length, 2);
  const denied = applyQuota(state, {
    ip: "198.51.100.1",
    nowMs: 3,
    limits,
    charge: "rate",
  });
  assert.equal(denied.allow, false);
  assert.equal(denied.status, 429);
  assert.equal(denied.code, "cap");
  assert.equal(Object.keys(state.knownPrefixes).length, 2);
  assert.equal("198.51.100.1" in state.knownPrefixes, false);

  const repeat = applyQuota(state, {
    ip: "203.0.113.10",
    nowMs: 4,
    limits,
    charge: "rate",
  });
  assert.equal(repeat.allow, true);
  assert.equal(state.knownPrefixes["203.0.113.10"].at, storedAt);
  assert.equal(Object.keys(state.knownPrefixes).length, 2);
});

test("rate-only allows count toward the daily row-write cap", () => {
  const state = emptyQuotaState();
  const limits = {
    ...LIMITS,
    dailyRowWrites: 2,
    perMinute: 100,
    newBucketsPerMinute: 100,
  };
  assert.equal(
    applyQuota(state, { ip: "203.0.113.10", nowMs: 1, limits, charge: "rate" }).allow,
    true,
  );
  assert.equal(
    applyQuota(state, { ip: "203.0.113.10", nowMs: 1, limits, charge: "rate" }).allow,
    true,
  );
  const denied = applyQuota(state, {
    ip: "203.0.113.10",
    nowMs: 1,
    limits,
    charge: "rate",
  });
  assert.equal(denied.allow, false);
  assert.equal(denied.status, 429);
  assert.equal(denied.code, "cap");
  assert.equal(state.rowWrites, 2);
});

test("MailboxQuota fails closed before a rate-only storage put that would exceed daily row writes", async () => {
  const memory = memoryQuotaStorage();
  const quota = new MailboxQuota(
    { storage: memory.storage },
    {
      // Each rate-only persist is KV + prefix + net (3 rows).
      MAILBOX_DAILY_ROW_WRITES: "6",
      MAILBOX_RATE_LIMIT_PER_MINUTE: "100",
      MAILBOX_NEW_BUCKETS_PER_MINUTE: "100",
    },
  );
  const first = await quotaCheck(quota, {
    ip: "203.0.113.10",
    nowMs: 1,
    charge: "rate",
  });
  const second = await quotaCheck(quota, {
    ip: "203.0.113.10",
    nowMs: 1,
    charge: "rate",
  });
  const third = await quotaCheck(quota, {
    ip: "203.0.113.10",
    nowMs: 1,
    charge: "rate",
  });
  assert.equal(first.status, 200);
  assert.equal(second.status, 200);
  assert.equal(third.status, 429);
  assert.equal((await third.json()).error, "cap");
  assert.equal(memory.puts, 2);
});

test("MailboxQuota fails closed when quota storage get throws", async () => {
  const memory = memoryQuotaStorage();
  memory.storage.get = async () => {
    throw new Error("unreadable");
  };
  const quota = new MailboxQuota({ storage: memory.storage }, {});
  const response = await quota.fetch(
    new Request("https://quota/check", {
      method: "POST",
      headers: {
        "CF-Connecting-IP": "203.0.113.10",
        "X-WebJam-Now": "1",
        "X-WebJam-Row-Writes": "0",
        "X-WebJam-Quota-Scope": "mailbox",
        "X-WebJam-Quota-Charge": "all",
      },
    }),
  );
  assert.equal(response.status, 503);
  assert.equal((await response.json()).error, "unavailable");
  assert.equal(memory.puts, 0);
  assert.equal(memory.data.size, 0);
});

test("more than 512 networks in a day still serve a new network", async () => {
  const memory = memoryQuotaStorage();
  const quota = new MailboxQuota(
    { storage: memory.storage },
    {
      MAILBOX_DAILY_REQUESTS: "10000",
      MAILBOX_DAILY_ROW_WRITES: "10000",
      MAILBOX_RATE_LIMIT_PER_MINUTE: "1000",
      MAILBOX_NEW_BUCKETS_PER_MINUTE: "1000",
    },
  );
  const check = (ip) =>
    quota.fetch(
      new Request("https://quota/check", {
        method: "POST",
        headers: {
          "CF-Connecting-IP": ip,
          "X-WebJam-Now": "1",
          "X-WebJam-Row-Writes": "0",
          "X-WebJam-Quota-Scope": "mailbox",
          "X-WebJam-Quota-Charge": "all",
        },
      }),
    );
  const seen = 520;
  for (let i = 0; i < seen; i += 1) {
    const ip = `198.${(i >> 8) & 255}.${i & 255}.10`;
    const response = await check(ip);
    assert.notEqual(response.status, 503);
    assert.equal(response.status, 200);
  }
  const next = await check("203.0.113.10");
  assert.notEqual(next.status, 503);
  assert.equal(next.status, 200);
  const stored = memory.data.get("quota:mailbox");
  assert.equal("netShares" in stored, false);
  assert.ok(memory.netRows.size > 512);
  assert.ok(memory.netRows.size >= seen + 1);
});

test("MailboxQuota counts prefix, net, and KV persists toward daily row writes", async () => {
  const memory = memoryQuotaStorage();
  const quota = new MailboxQuota(
    { storage: memory.storage },
    {
      MAILBOX_DAILY_ROW_WRITES: "3",
      MAILBOX_RATE_LIMIT_PER_MINUTE: "100",
      MAILBOX_NEW_BUCKETS_PER_MINUTE: "100",
    },
  );
  const check = () =>
    quota.fetch(
      new Request("https://quota/check", {
        method: "POST",
        headers: {
          "CF-Connecting-IP": "203.0.113.10",
          "X-WebJam-Now": "1",
          "X-WebJam-Row-Writes": "0",
          "X-WebJam-Quota-Scope": "mailbox",
          "X-WebJam-Quota-Charge": "all",
        },
      }),
    );
  const first = await check();
  const second = await check();
  assert.equal(first.status, 200);
  assert.equal(second.status, 429);
  assert.equal((await second.json()).error, "cap");
  assert.equal(memory.puts, 1);
  assert.equal(memory.data.get("quota:mailbox").rowWrites, 3);
});

test("30k prefixes from one /32 still serve other users and never 503", async () => {
  const memory = memoryQuotaStorage();
  const quota = new MailboxQuota(
    { storage: memory.storage },
    {
      MAILBOX_DAILY_REQUESTS: "100000",
      MAILBOX_DAILY_ROW_WRITES: "100000",
      MAILBOX_DAILY_GB_S: "100000",
      MAILBOX_NET_DAILY_REQUESTS: "100000",
      MAILBOX_NET_DAILY_ROW_WRITES: "100000",
      MAILBOX_NET_DAILY_GB_S: "100000",
      MAILBOX_PREFIX_DAILY_REQUESTS: "2",
      MAILBOX_RATE_LIMIT_PER_MINUTE: "100000",
      MAILBOX_NEW_BUCKETS_PER_MINUTE: "100000",
      MAILBOX_RATE_PREFIX_DAILY: "100000",
    },
  );
  const check = (ip, nowMs) =>
    quota.fetch(
      new Request("https://quota/check", {
        method: "POST",
        headers: {
          "CF-Connecting-IP": ip,
          "X-WebJam-Now": String(nowMs),
          "X-WebJam-Row-Writes": "0",
          "X-WebJam-Quota-Scope": "mailbox",
          "X-WebJam-Quota-Charge": "all",
        },
      }),
    );
  const flood = 30_000;
  const other = "198.51.100.50";
  for (let i = 0; i < flood; i += 1) {
    const ip = `2001:db8:${i.toString(16)}::1`;
    const response = await check(ip, 1);
    assert.notEqual(response.status, 503);
    assert.equal(response.status, 200);
  }
  const stored = memory.data.get("quota:mailbox");
  assert.equal(stored.knownPrefixes, undefined);
  assert.equal(stored.netShares, undefined);
  const encoded = JSON.stringify(stored);
  assert.ok(encoded.length < MAX_QUOTA_VALUE_BYTES);
  assert.ok(encoded.length < 32 * 1024);
  assert.equal("knownPrefixes" in stored, false);
  assert.equal("netShares" in stored, false);
  assert.deepEqual(stored, compactQuotaState(stored));
  assert.ok(memory.prefixRows.size >= flood);
  const outsider = await check(other, 1);
  assert.notEqual(outsider.status, 503);
  assert.equal(outsider.status, 200);
  const firstAgain = await check("2001:db8::1", 1);
  assert.notEqual(firstAgain.status, 503);
  assert.equal(firstAgain.status, 200);
  assert.equal(prefixNet("2001:db8:1::1"), "2001:db8::/32");
  assert.equal(prefixNet("2001:db8:7530::1"), "2001:db8::/32");
  assert.notEqual(prefixNet(other), "2001:db8::/32");
});

test("rate-only requests cannot bypass prefix or net write shares", async () => {
  const prefixLimits = {
    ...LIMITS,
    dailyRowWrites: 20,
    prefixDailyRowWrites: 2,
    netDailyRowWrites: 20,
    perMinute: 100,
    newBucketsPerMinute: 100,
  };
  const prefixState = emptyQuotaState();
  assert.equal(
    applyQuota(prefixState, {
      ip: "203.0.113.10",
      nowMs: 1,
      limits: prefixLimits,
      charge: "rate",
    }).allow,
    true,
  );
  assert.equal(
    applyQuota(prefixState, {
      ip: "203.0.113.10",
      nowMs: 1,
      limits: prefixLimits,
      charge: "rate",
    }).allow,
    true,
  );
  const prefixDenied = applyQuota(prefixState, {
    ip: "203.0.113.10",
    nowMs: 1,
    limits: prefixLimits,
    charge: "rate",
  });
  assert.equal(prefixDenied.allow, false);
  assert.equal(prefixDenied.code, "cap");
  assert.ok(prefixState.rowWrites <= 2);
  assert.ok((prefixState.knownPrefixes["203.0.113.10"]?.rowWrites ?? 0) <= 2);
  assert.equal(
    applyQuota(prefixState, {
      ip: "198.51.100.1",
      nowMs: 1,
      limits: prefixLimits,
      charge: "rate",
    }).allow,
    true,
  );

  const netLimits = {
    ...LIMITS,
    dailyRowWrites: 20,
    prefixDailyRowWrites: 20,
    netDailyRowWrites: 2,
    perMinute: 100,
    newBucketsPerMinute: 100,
  };
  const netState = emptyQuotaState();
  assert.equal(
    applyQuota(netState, {
      ip: "203.0.113.10",
      nowMs: 1,
      limits: netLimits,
      charge: "rate",
    }).allow,
    true,
  );
  assert.equal(
    applyQuota(netState, {
      ip: "203.0.113.11",
      nowMs: 1,
      limits: netLimits,
      charge: "rate",
    }).allow,
    true,
  );
  const netDenied = applyQuota(netState, {
    ip: "203.0.113.12",
    nowMs: 1,
    limits: netLimits,
    charge: "rate",
  });
  assert.equal(netDenied.allow, false);
  assert.equal(netDenied.code, "cap");
  assert.equal(
    applyQuota(netState, {
      ip: "198.51.100.1",
      nowMs: 1,
      limits: netLimits,
      charge: "rate",
    }).allow,
    true,
  );

  const memory = memoryQuotaStorage();
  const quota = new MailboxQuota(
    { storage: memory.storage },
    {
      MAILBOX_DAILY_ROW_WRITES: "20",
      MAILBOX_PREFIX_DAILY_ROW_WRITES: "6",
      MAILBOX_NET_DAILY_ROW_WRITES: "20",
      MAILBOX_RATE_LIMIT_PER_MINUTE: "100",
      MAILBOX_NEW_BUCKETS_PER_MINUTE: "100",
    },
  );
  let allowed = 0;
  for (let i = 0; i < 10; i += 1) {
    const response = await quotaCheck(quota, {
      ip: "203.0.113.10",
      nowMs: 1,
      charge: "rate",
    });
    if (response.status === 200) {
      allowed += 1;
    } else {
      assert.equal(response.status, 429);
      assert.equal((await response.json()).error, "cap");
    }
  }
  assert.ok(allowed <= 2);
  assert.ok(memory.puts <= 2);
  assert.ok((memory.data.get("quota:mailbox")?.rowWrites ?? 0) <= 6);
  const outsider = await quotaCheck(quota, {
    ip: "198.51.100.1",
    nowMs: 1,
    charge: "rate",
  });
  assert.equal(outsider.status, 200);
  assert.ok((memory.data.get("quota:mailbox")?.rowWrites ?? 0) < 20);
});

test("rate-only prefix inserts count toward daily row writes before they execute", async () => {
  const memory = memoryQuotaStorage();
  const quota = new MailboxQuota(
    { storage: memory.storage },
    {
      MAILBOX_DAILY_ROW_WRITES: "1",
      MAILBOX_RATE_LIMIT_PER_MINUTE: "100",
      MAILBOX_NEW_BUCKETS_PER_MINUTE: "100",
    },
  );
  const response = await quotaCheck(quota, {
    ip: "203.0.113.10",
    nowMs: 1,
    charge: "rate",
  });
  assert.equal(response.status, 429);
  assert.equal((await response.json()).error, "cap");
  assert.equal(memory.puts, 0);
  assert.equal(memory.prefixRows.size, 0);
  assert.equal(memory.netRows.size, 0);
  assert.equal(memory.stats.rowMutations, 0);
});

test("expiry deletes are admitted as deleted rows and do not run before the daily cap", async () => {
  const day1 = Date.parse("2026-10-15T00:00:00Z");
  const day2 = Date.parse("2026-10-16T00:00:00Z");
  const deniedMemory = memoryQuotaStorage();
  const deniedEnv = {
    MAILBOX_DAILY_ROW_WRITES: "1000",
    MAILBOX_RATE_LIMIT_PER_MINUTE: "100",
    MAILBOX_NEW_BUCKETS_PER_MINUTE: "100",
  };
  const deniedQuota = new MailboxQuota({ storage: deniedMemory.storage }, deniedEnv);
  const seedIps = ["203.0.113.10", "198.51.100.1", "192.0.2.8", "203.0.114.10", "198.51.101.1"];
  for (const ip of seedIps) {
    const seeded = await quotaCheck(deniedQuota, {
      ip,
      nowMs: day1,
      charge: "all",
    });
    assert.equal(seeded.status, 200);
  }
  const seededPrefixes = deniedMemory.prefixRows.size;
  const seededNets = deniedMemory.netRows.size;
  assert.ok(seededPrefixes >= 5);
  deniedEnv.MAILBOX_DAILY_ROW_WRITES = "2";
  const denied = await quotaCheck(deniedQuota, {
    ip: "198.51.100.1",
    nowMs: day2,
    charge: "all",
  });
  assert.equal(denied.status, 429);
  assert.equal((await denied.json()).error, "cap");
  assert.equal(deniedMemory.prefixRows.size, seededPrefixes);
  assert.equal(deniedMemory.netRows.size, seededNets);

  const countedMemory = memoryQuotaStorage();
  const countedQuota = new MailboxQuota(
    { storage: countedMemory.storage },
    {
      MAILBOX_DAILY_ROW_WRITES: "1000",
      MAILBOX_RATE_LIMIT_PER_MINUTE: "100",
      MAILBOX_NEW_BUCKETS_PER_MINUTE: "100",
    },
  );
  for (const ip of seedIps) {
    const seeded = await quotaCheck(countedQuota, {
      ip,
      nowMs: day1,
      charge: "all",
    });
    assert.equal(seeded.status, 200);
  }
  const beforeMutations = countedMemory.storageMutations;
  const beforeDeletes = countedMemory.stats.deletedRows;
  const rolled = await quotaCheck(countedQuota, {
    ip: "203.0.115.9",
    nowMs: day2,
    charge: "all",
  });
  assert.equal(rolled.status, 200);
  const deleted = countedMemory.stats.deletedRows - beforeDeletes;
  assert.ok(deleted >= 5);
  const charged = countedMemory.data.get("quota:mailbox").rowWrites;
  const persistMutations = countedMemory.storageMutations - beforeMutations;
  assert.equal(charged, persistMutations);
  assert.ok(charged >= 3 + deleted);
  assert.notEqual(charged, 3 + 2);
});

test("quota admission charges every storage row across mixed request types and never exceeds the daily cap", async () => {
  const cap = 40;
  const memory = memoryQuotaStorage();
  const quota = new MailboxQuota(
    { storage: memory.storage },
    {
      MAILBOX_DAILY_ROW_WRITES: String(cap),
      MAILBOX_PREFIX_DAILY_ROW_WRITES: String(cap),
      MAILBOX_NET_DAILY_ROW_WRITES: String(cap),
      MAILBOX_DAILY_REQUESTS: "100",
      MAILBOX_RATE_LIMIT_PER_MINUTE: "100",
      MAILBOX_NEW_BUCKETS_PER_MINUTE: "100",
    },
  );
  const env = {
    MAILBOX_QUOTA: {
      idFromName() {
        return "mailbox";
      },
      get() {
        return { fetch: (request) => quota.fetch(request) };
      },
    },
    MAILBOX_DAILY_ROW_WRITES: String(cap),
    MAILBOX_PREFIX_DAILY_ROW_WRITES: String(cap),
    MAILBOX_NET_DAILY_ROW_WRITES: String(cap),
    MAILBOX_DAILY_REQUESTS: "100",
    MAILBOX_RATE_LIMIT_PER_MINUTE: "100",
    MAILBOX_NEW_BUCKETS_PER_MINUTE: "100",
  };
  const mailboxFetch = memoryMailbox();
  let mailboxWritesCharged = 0;
  const snapshotMutations = () => memory.storageMutations;
  const assertInvariant = (allowed) => {
    const stored = memory.data.get("quota:mailbox");
    const charged = stored ? Number(stored.rowWrites) || 0 : 0;
    assert.ok(charged <= cap);
    assert.equal(charged, memory.storageMutations + mailboxWritesCharged);
    if (!allowed) {
      assert.equal(memory.storageMutations, snapshotBefore);
    }
  };

  let snapshotBefore = snapshotMutations();
  const posted = await handleRequest(
    mailboxRequest("POST", "answer", { payload: PAYLOAD }),
    env,
    { mailboxFetch, nowMs: 1 },
  );
  assert.equal(posted.status, 201);
  mailboxWritesCharged += 2;
  assertInvariant(true);

  snapshotBefore = snapshotMutations();
  const polled = await handleRequest(
    mailboxRequest("GET", "answer"),
    env,
    { mailboxFetch, nowMs: 1 },
  );
  assert.equal(polled.status, 200);
  assertInvariant(true);

  snapshotBefore = snapshotMutations();
  const failedUpgrade = await handleRequest(
    mailboxRequest("GET", "answer", undefined, {
      "CF-Connecting-IP": "203.0.113.10",
      Upgrade: "websocket",
    }),
    env,
    {
      mailboxFetch: async () =>
        new Response(JSON.stringify({ error: "rate" }), {
          status: 429,
          headers: { "content-type": "application/json" },
        }),
      nowMs: 1,
    },
  );
  assert.equal(failedUpgrade.status, 429);
  assertInvariant(true);

  snapshotBefore = snapshotMutations();
  const upgraded = await handleRequest(
    mailboxRequest("GET", "answer", undefined, {
      "CF-Connecting-IP": "198.51.100.1",
      Upgrade: "websocket",
    }),
    env,
    {
      mailboxFetch: async () => ({ status: 101 }),
      nowMs: 1,
    },
  );
  assert.equal(upgraded.status, 101);
  assertInvariant(true);

  const day1 = Date.parse("2026-10-15T00:00:00Z");
  const day2 = Date.parse("2026-10-16T00:00:00Z");
  resetWorkerState();
  const rolloverMemory = memoryQuotaStorage();
  const rolloverQuota = new MailboxQuota(
    { storage: rolloverMemory.storage },
    {
      MAILBOX_DAILY_ROW_WRITES: String(cap),
      MAILBOX_PREFIX_DAILY_ROW_WRITES: String(cap),
      MAILBOX_NET_DAILY_ROW_WRITES: String(cap),
      MAILBOX_RATE_LIMIT_PER_MINUTE: "100",
      MAILBOX_NEW_BUCKETS_PER_MINUTE: "100",
    },
  );
  const seedNets = ["203.0.113.20", "198.51.100.20", "192.0.2.20", "203.0.114.20"];
  for (const ip of seedNets) {
    const seeded = await quotaCheck(rolloverQuota, {
      ip,
      nowMs: day1,
      writes: 1,
      charge: "all",
    });
    assert.equal(seeded.status, 200);
  }
  const rolloverCharged = () => {
    const stored = rolloverMemory.data.get("quota:mailbox");
    return stored ? Number(stored.rowWrites) || 0 : 0;
  };
  assert.equal(rolloverCharged(), rolloverMemory.storageMutations + 4);
  assert.ok(rolloverCharged() <= cap);

  const mutationsAtRollover = rolloverMemory.storageMutations;
  const expired = await quotaCheck(rolloverQuota, {
    ip: "198.51.101.9",
    nowMs: day2,
    writes: 1,
    charge: "all",
  });
  assert.equal(expired.status, 200);
  const todayMutations = rolloverMemory.storageMutations - mutationsAtRollover;
  assert.equal(rolloverCharged(), todayMutations + 1);
  assert.ok(rolloverCharged() <= cap);
  assert.ok(rolloverCharged() >= 1 + 3);

  const mutationsBeforeDeny = rolloverMemory.storageMutations;
  const tight = await quotaCheck(rolloverQuota, {
    ip: "203.0.113.10",
    nowMs: day2,
    writes: cap,
    charge: "all",
  });
  assert.equal(tight.status, 429);
  assert.equal(rolloverMemory.storageMutations, mutationsBeforeDeny);
  assert.ok(rolloverCharged() <= cap);
});

test("http mailbox requests are rejected", async () => {
  resetWorkerState();
  const denied = await handleRequest(
    new Request("http://worker.test/mailbox/" + ID + "/answer", { method: "GET" }),
    {},
    { mailboxFetch: memoryMailbox(), nowMs: 1 },
  );
  assert.equal(denied.status, 403);
  assert.deepEqual(await denied.json(), { error: "https" });
});
