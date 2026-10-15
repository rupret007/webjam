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
  DEFAULT_DAILY_GB_S,
  DEFAULT_DAILY_REQUESTS,
  DEFAULT_DAILY_ROW_WRITES,
  DEFAULT_MAILBOX_RATE_LIMIT,
  DEFAULT_NEW_BUCKETS_PER_MINUTE,
  DEFAULT_PREFIX_DAILY_REQUESTS,
  DEFAULT_RATE_LIMIT,
  emptyQuotaState,
  ESTIMATED_GB_S_PER_REQUEST,
  MailboxQuota,
  MAX_IP_BUCKETS,
  prefix48,
  prefixNet,
  quotaLimits,
  RATE_WINDOW_MS,
  rateKey,
} from "../src/quota.js";
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
  let puts = 0;
  const data = new Map();
  const quota = new MailboxQuota(
    {
      storage: {
        async get(key) {
          return data.get(key);
        },
        async put(key, value) {
          puts += 1;
          data.set(key, value);
        },
      },
    },
    {
      MAILBOX_DAILY_ROW_WRITES: "2",
      MAILBOX_RATE_LIMIT_PER_MINUTE: "100",
      MAILBOX_NEW_BUCKETS_PER_MINUTE: "100",
    },
  );
  const check = (nowMs) =>
    quota.fetch(
      new Request("https://quota/check", {
        method: "POST",
        headers: {
          "CF-Connecting-IP": "203.0.113.10",
          "X-WebJam-Now": String(nowMs),
          "X-WebJam-Row-Writes": "0",
          "X-WebJam-Quota-Scope": "mailbox",
          "X-WebJam-Quota-Charge": "rate",
        },
      }),
    );
  const first = await check(1);
  const second = await check(1);
  const third = await check(1);
  assert.equal(first.status, 200);
  assert.equal(second.status, 200);
  assert.equal(third.status, 429);
  assert.equal((await third.json()).error, "cap");
  assert.equal(puts, 2);
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
