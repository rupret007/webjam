import assert from "node:assert/strict";
import test from "node:test";

import { originAllowed } from "../src/browser.js";
import {
  DEFAULT_WEB_DAILY_REQUESTS,
  quotaLimits,
} from "../src/quota.js";
import { handleRequest, resetWorkerState } from "../src/worker.js";

const ID = "ab".repeat(32);
const PAYLOAD = "c2lnbmFs";
const ALLOWED_ORIGIN = "http://localhost:8787";
const SESSION_UUID = "6ba7b810-9dad-11d1-80b4-00c04fd430c8";
const INVITE = "A".repeat(43);

function memoryMailbox() {
  const rooms = new Map();
  return async (request, mailbox) => {
    const slot = mailbox.slot || "offer";
    const room = rooms.get(mailbox.id) ?? { offer: "", answer: "" };
    if (request.method === "POST") {
      const body = await request.json();
      if (room[slot]) {
        return new Response(JSON.stringify({ error: "duplicate" }), { status: 409 });
      }
      room[slot] = String(body.payload ?? "");
      rooms.set(mailbox.id, room);
      return new Response(JSON.stringify({ ok: true }), { status: 201 });
    }
    if (!room[slot]) {
      return new Response(null, { status: 204 });
    }
    return new Response(JSON.stringify({ payload: room[slot] }), { status: 200 });
  };
}

function browserMailboxGet(headers = {}) {
  return new Request(`https://worker.test/mailbox/${ID}/offer`, {
    method: "GET",
    headers: {
      Origin: ALLOWED_ORIGIN,
      "CF-Connecting-IP": "203.0.113.10",
      ...headers,
    },
  });
}

function browserTurnPost(body, headers = {}) {
  return new Request("https://worker.test/turn-credentials", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      Origin: ALLOWED_ORIGIN,
      "CF-Connecting-IP": "203.0.113.10",
      ...headers,
    },
    body: JSON.stringify(body),
  });
}

test("origin allowlist accepts localhost dev ports", () => {
  assert.equal(originAllowed("http://localhost:5173", {}), true);
  assert.equal(originAllowed("https://evil.example", {}), false);
});

function turnEnv() {
  return {
    TURN_KEY_ID: "webjam-relay-key-id",
    TURN_KEY_API_TOKEN: "test-turn-api-token",
    CLOUDFLARE_ACCOUNT_ID: "test-account",
    CLOUDFLARE_ANALYTICS_API_TOKEN: "test-analytics-token",
    TURN_MONTHLY_CAP_GB: "800",
    MAILBOX_DAILY_REQUESTS: "10000",
  };
}

test("Origin-present POST /turn-credentials missing client=web is rejected", async () => {
  resetWorkerState();
  const response = await handleRequest(
    browserTurnPost({
      session_id: SESSION_UUID,
      invite_token: INVITE,
    }),
    turnEnv(),
    { mailboxFetch: memoryMailbox(), nowMs: 1 },
  );
  assert.equal(response.status, 403);
});

test("Origin-present /mailbox/:id/:slot charges web sub-cap and global daily cap", async () => {
  resetWorkerState();
  const mailboxFetch = memoryMailbox();
  const env = {
    MAILBOX_DAILY_REQUESTS: "10000",
    WEB_DAILY_REQUESTS: "1",
    MAILBOX_RATE_LIMIT_PER_MINUTE: "100",
  };
  const limits = quotaLimits(env, "web-mailbox");
  assert.equal(limits.dailyRequests, 1);
  assert.ok(DEFAULT_WEB_DAILY_REQUESTS > 0);

  const first = await handleRequest(browserMailboxGet(), env, { mailboxFetch, nowMs: 10 });
  assert.equal(first.status, 204);

  const second = await handleRequest(browserMailboxGet(), env, { mailboxFetch, nowMs: 11 });
  assert.equal(second.status, 429);

  const desktop = await handleRequest(
    new Request(`https://worker.test/mailbox/${ID}/offer`, {
      method: "GET",
      headers: { "CF-Connecting-IP": "203.0.113.10" },
    }),
    env,
    { mailboxFetch, nowMs: 12 },
  );
  assert.equal(desktop.status, 204);
});
