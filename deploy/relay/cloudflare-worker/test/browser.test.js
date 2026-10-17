import assert from "node:assert/strict";
import test from "node:test";

import { originAllowed } from "../src/browser.js";
import {
  DEFAULT_WEB_DAILY_REQUESTS,
  MailboxQuota,
  quotaLimits,
} from "../src/quota.js";
import { handleRequest, resetWorkerState } from "../src/worker.js";
import { memoryQuotaStorage, quotaCheck } from "./quota-memory.js";

const ID = "ab".repeat(32);
const PAYLOAD = "c2lnbmFs";
const ALLOWED_ORIGIN = "http://localhost:8787";

function corsDevEnv(extra = {}) {
  return { WEBJAM_CORS_DEV_LOCALHOST: "1", ...extra };
}
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

test("origin allowlist accepts localhost dev ports only when dev flag is set", () => {
  assert.equal(originAllowed("http://localhost:5173", {}), false);
  assert.equal(originAllowed("http://localhost:5173", corsDevEnv()), true);
  assert.equal(originAllowed("https://stalemate.band", {}), true);
  assert.equal(originAllowed("https://evil.example", {}), false);
  assert.equal(originAllowed("http://localhost:0", corsDevEnv()), false);
  assert.equal(originAllowed("http://localhost:999999", corsDevEnv()), false);
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
    corsDevEnv(turnEnv()),
    { mailboxFetch: memoryMailbox(), nowMs: 1 },
  );
  assert.equal(response.status, 403);
});

test("Origin-present /mailbox/:id/:slot charges web sub-cap and global daily cap", async () => {
  resetWorkerState();
  const mailboxFetch = memoryMailbox();
  const env = corsDevEnv({
    MAILBOX_DAILY_REQUESTS: "10000",
    WEB_DAILY_REQUESTS: "1",
    MAILBOX_RATE_LIMIT_PER_MINUTE: "100",
  });
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

test("CORS preflight OPTIONS and disallowed Origin return expected status and headers before touching DO", async () => {
  resetWorkerState();
  let mailboxTouches = 0;
  const mailboxFetch = async (...args) => {
    mailboxTouches += 1;
    return memoryMailbox()(...args);
  };
  const preflight = await handleRequest(
    new Request(`https://worker.test/mailbox/${ID}/offer`, {
      method: "OPTIONS",
      headers: {
        Origin: ALLOWED_ORIGIN,
        "CF-Connecting-IP": "203.0.113.10",
      },
    }),
    corsDevEnv(),
    { mailboxFetch, nowMs: 1 },
  );
  assert.equal(preflight.status, 204);
  assert.equal(preflight.headers.get("access-control-allow-origin"), ALLOWED_ORIGIN);
  assert.equal(mailboxTouches, 0);

  const blocked = await handleRequest(
    browserMailboxGet({ Origin: "https://evil.example" }),
    corsDevEnv(),
    { mailboxFetch, nowMs: 2 },
  );
  assert.equal(blocked.status, 403);
  assert.equal(mailboxTouches, 0);
});

test("enforceAccess hook runs for global and web scopes on browser mailbox traffic", async () => {
  resetWorkerState();
  const scopes = [];
  await handleRequest(browserMailboxGet(), corsDevEnv(), {
    mailboxFetch: memoryMailbox(),
    nowMs: 1,
    enforceAccess: (_request, _nowMs, _writes, _charge, scope) => {
      scopes.push(scope);
      return null;
    },
  });
  assert.deepEqual(scopes, ["mailbox", "web-mailbox"]);
});

test("MailboxQuota DO enforces web-mailbox and web-turn scopes", async () => {
  const memory = memoryQuotaStorage();
  const quotaEnv = {
    MAILBOX_DAILY_REQUESTS: "10000",
    WEB_DAILY_REQUESTS: "1",
    MAILBOX_RATE_LIMIT_PER_MINUTE: "100",
    TURN_RATE_LIMIT_PER_MINUTE: "100",
  };
  const quota = new MailboxQuota({ storage: memory.storage }, quotaEnv);
  const ip = "203.0.113.10";
  const firstMailbox = await quotaCheck(quota, { ip, nowMs: 10, scope: "web-mailbox" });
  const secondMailbox = await quotaCheck(quota, { ip, nowMs: 11, scope: "web-mailbox" });
  assert.equal(firstMailbox.status, 200);
  assert.equal(secondMailbox.status, 429);
  assert.equal(memory.data.get("quota:web-mailbox") != null, true);

  const turnMemory = memoryQuotaStorage();
  const turnQuota = new MailboxQuota({ storage: turnMemory.storage }, {
    MAILBOX_DAILY_REQUESTS: "1",
    WEB_DAILY_REQUESTS: "10000",
    MAILBOX_RATE_LIMIT_PER_MINUTE: "100",
    TURN_RATE_LIMIT_PER_MINUTE: "100",
  });
  const firstTurn = await quotaCheck(turnQuota, { ip, nowMs: 20, scope: "web-turn" });
  const secondTurn = await quotaCheck(turnQuota, { ip, nowMs: 21, scope: "web-turn" });
  assert.equal(firstTurn.status, 200);
  assert.equal(secondTurn.status, 200);
  assert.equal(turnMemory.data.get("quota:web-turn") != null, true);
  const globalMailbox = await quotaCheck(turnQuota, { ip, nowMs: 22, scope: "mailbox" });
  const globalMailboxCap = await quotaCheck(turnQuota, { ip, nowMs: 23, scope: "mailbox" });
  assert.equal(globalMailbox.status, 200);
  assert.equal(globalMailboxCap.status, 429);
});
