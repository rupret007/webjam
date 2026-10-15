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
  emptyQuotaState,
  ESTIMATED_GB_S_PER_REQUEST,
  MAX_IP_BUCKETS,
  quotaLimits,
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
  for (let i = 0; i < 10; i += 1) {
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
