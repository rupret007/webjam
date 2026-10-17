import assert from "node:assert/strict";
import test from "node:test";
import { handleRequest, resetWorkerState } from "../src/worker.js";
import { allowedOrigin, withCors } from "../src/cors.js";
import { credentialTtlSeconds, monthlyCapBytes, webMonthlyCapBytes } from "../src/policy.js";
import { MailboxQuota, quotaLimits } from "../src/quota.js";
import { memoryQuotaStorage } from "./quota-memory.js";

const ORIGIN = "https://stalemate.band";
const SESSION = "6ba7b810-9dad-11d1-80b4-00c04fd430c8";
const INVITE = "A".repeat(43);
const ID = "ab".repeat(32);
const PAYLOAD = "dGVzdC1jaXBoZXJ0ZXh0";
const NOW = Date.parse("2026-10-15T00:00:00Z");
function quotaBinding() {
  return {
    idFromName: () => "quota",
    get: () => ({ fetch: async () => new Response(null, { status: 200 }) }),
  };
}

const env = {
  TURN_KEY_ID: "desktop-key", TURN_KEY_API_TOKEN: "desktop-test-token",
  WEB_TURN_KEY_ID: "web-key", WEB_TURN_KEY_API_TOKEN: "web-test-token",
  CLOUDFLARE_ACCOUNT_ID: "account", CLOUDFLARE_ANALYTICS_API_TOKEN: "analytics-test-token",
  MAILBOX_QUOTA: quotaBinding(),
};

function request(path = "/turn-credentials", body, origin = ORIGIN, method = "POST") {
  const headers = { "CF-Connecting-IP": "203.0.113.10" };
  if (origin !== null) headers.Origin = origin;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  return new Request(`https://worker.test${path}`, {
    method, headers, ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
}

const token = (extra = {}) => ({ session_id: SESSION, invite_token: INVITE, client: "web", ...extra });
function analytics(desktop = 0, web = 0) {
  const groups = (bytes) => [{ sum: { ingressBytes: bytes, egressBytes: 0 } }];
  return { data: { viewer: { accounts: [{
    callsTurnUsageAdaptiveGroups: groups(desktop), webUsage: groups(web),
  }] } } };
}

function upstream(payload = analytics(), returnedTtl = 600) {
  const calls = [];
  const fetch = async (url, init) => {
    calls.push({ url, init, body: JSON.parse(init.body) });
    if (url.includes("graphql")) {
      return Response.json(payload);
    }
    return Response.json({ iceServers: [{ urls: "turn:relay.example:3478", username: "user", credential: "temporary" }], ttl: returnedTtl });
  };
  return { fetch, calls, nowMs: NOW };
}

test("CORS denies unlisted, deceptive, opaque and malformed origins before any Durable Object", async () => {
  let touched = 0;
  const binding = { idFromName() { touched += 1; throw Error("must not touch"); } };
  for (const origin of ["https://evil.example", "https://stalemate.band.evil.example", "http://stalemate.band", "null", "https://stalemate.band/", "https://user@stalemate.band"]) {
    for (const method of ["GET", "OPTIONS"]) {
      const response = await handleRequest(request(`/mailbox/${ID}/offer`, undefined, origin, method), {
        MAILBOX: binding, MAILBOX_QUOTA: binding,
      });
      assert.equal(response.status, 403);
      assert.equal(response.headers.has("Access-Control-Allow-Origin"), false);
    }
  }
  assert.equal(touched, 0);
});

test("http preflight OPTIONS is rejected with 403 https", async () => {
  let touched = 0;
  const binding = { idFromName() { touched += 1; throw Error("must not touch"); } };
  const req = new Request(`http://worker.test/mailbox/${ID}/offer`, {
    method: "OPTIONS",
    headers: {
      Origin: ORIGIN,
      "Access-Control-Request-Method": "POST",
      "Access-Control-Request-Headers": "content-type",
    },
  });
  const response = await handleRequest(req, { MAILBOX: binding, MAILBOX_QUOTA: binding });
  assert.equal(response.status, 403);
  assert.deepEqual(await response.json(), { error: "https" });
  assert.equal(touched, 0);
});

test("websocket 101 response with allowed Origin does not throw in withCors", () => {
  const response = { status: 101, headers: new Headers(), body: null };
  assert.equal(withCors(response, ORIGIN), response);
});

test("preflight validates methods and headers and never calls any Durable Object", async () => {
  let touched = 0;
  const binding = { idFromName() { touched += 1; throw Error("must not touch"); } };
  for (const [method, headers, status] of [["POST", "content-type", 204], ["DELETE", "", 204], ["PUT", "", 403], ["POST", "X-Admin", 403]]) {
    const req = new Request(`https://worker.test/mailbox/${ID}/offer`, {
      method: "OPTIONS", headers: { Origin: ORIGIN, "Access-Control-Request-Method": method, "Access-Control-Request-Headers": headers },
    });
    const response = await handleRequest(req, { MAILBOX: binding, MAILBOX_QUOTA: binding });
    assert.equal(response.status, status);
    if (status === 204) {
      assert.equal(response.headers.get("Access-Control-Allow-Origin"), ORIGIN);
      assert.match(response.headers.get("Vary"), /Origin/);
      assert.equal(response.headers.get("Access-Control-Allow-Headers"), "Content-Type");
      assert.equal(response.headers.has("Access-Control-Allow-Credentials"), false);
    }
  }
  assert.equal(touched, 0);
});

test("preview and dev loopback use exact configured origins, never wildcard patterns", () => {
  const config = { CORS_ALLOWED_ORIGINS: `${ORIGIN},https://preview.example,http://localhost:5173,http://127.0.0.1:4173,https://*.example,*` };
  assert.equal(allowedOrigin(request("/", undefined, "https://preview.example"), config), "https://preview.example");
  assert.equal(allowedOrigin(request("/", undefined, "https://other.example"), config), false);
  assert.equal(allowedOrigin(request("/", undefined, "https://*.example"), config), false);
  for (const origin of ["http://localhost:5173", "http://127.0.0.1:4173"]) {
    assert.equal(allowedOrigin(request("/", undefined, origin), config), false);
    assert.equal(allowedOrigin(request("/", undefined, origin), { ...config, ENVIRONMENT: "development" }), origin);
  }
  assert.equal(allowedOrigin(request("/", undefined, "http://localhost:5174"), { ...config, ENVIRONMENT: "development" }), false);
});

test("allowed origins receive CORS on success, empty mailbox responses and errors", async () => {
  resetWorkerState();
  for (const status of [200, 204, 429, 503]) {
    const response = await handleRequest(request(`/mailbox/${ID}/offer`, undefined, ORIGIN, "GET"), env, {
      nowMs: NOW, mailboxFetch: async () => new Response(null, { status, headers: { Vary: "Accept" } }),
    });
    assert.equal(response.status, status);
    assert.equal(response.headers.get("Access-Control-Allow-Origin"), ORIGIN);
    assert.equal(response.headers.get("Vary"), "Accept, Origin");
  }
  const desktop = await handleRequest(request(`/mailbox/${ID}/offer`, undefined, null, "GET"), {}, {
    nowMs: NOW, mailboxFetch: async () => new Response(null, { status: 204 }),
  });
  assert.equal(desktop.headers.has("Access-Control-Allow-Origin"), false);
});

test("web credentials require desktop token shapes and an explicit web client field", async () => {
  for (const invalid of [token({ client: undefined }), token({ client: "desktop" }), token({ session_id: "short" }), token({ invite_token: "short" }), token({ session_id: [SESSION] }), token({ invite_token: [INVITE] })]) {
    resetWorkerState();
    const deps = upstream();
    const response = await handleRequest(request("/turn-credentials", invalid), env, deps);
    assert.equal(response.status, 403);
    assert.equal(deps.calls.length, 0);
  }
  for (const session_id of [SESSION, ID]) {
    resetWorkerState();
    const response = await handleRequest(request("/turn-credentials", token({ session_id })), env, upstream());
    assert.equal(response.status, 200);
  }
});

test("web key is distinct and required, and production web quotas cannot fall back to isolate memory", async () => {
  for (const config of [{ ...env, WEB_TURN_KEY_ID: "" }, { ...env, WEB_TURN_KEY_API_TOKEN: "" }, { ...env, WEB_TURN_KEY_ID: env.TURN_KEY_ID }]) {
    resetWorkerState();
    const response = await handleRequest(request("/turn-credentials", token()), config, upstream());
    assert.equal(response.status, 503);
    assert.equal((await response.json()).error, "misconfigured");
  }
  for (const MAILBOX_QUOTA of [undefined, {}, { idFromName: (scope) => scope }]) {
    const response = await handleRequest(request(`/mailbox/${ID}/offer`, undefined, ORIGIN, "GET"), { ...env, MAILBOX_QUOTA });
    assert.equal(response.status, 503);
    assert.equal((await response.json()).error, "misconfigured");
  }
});

test("production web turn-credentials refuses missing MAILBOX_QUOTA before upstream calls", async () => {
  resetWorkerState();
  const deps = upstream();
  const response = await handleRequest(request("/turn-credentials", token()), {
    ...env,
    ENVIRONMENT: "production",
    MAILBOX_QUOTA: undefined,
  }, deps);
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: "misconfigured" });
  assert.equal(deps.calls.length, 0);
});

test("web monthly subcap and summed global cap fail closed without minting", async () => {
  for (const [desktop, web, client] of [[0, 200e9, "web"], [650e9, 150e9, "web"], [650e9, 150e9, undefined]]) {
    resetWorkerState();
    const deps = upstream(analytics(desktop, web));
    const response = await handleRequest(request("/turn-credentials", token({ client }), client ? ORIGIN : null), env, deps);
    assert.equal(response.status, 429);
    assert.deepEqual(await response.json(), { error: "cap" });
    assert.equal(deps.calls.length, 1);
  }
});

test("missing or unreadable either analytics stream is 503 and no credentials", async () => {
  const missingWeb = analytics();
  delete missingWeb.data.viewer.accounts[0].webUsage;
  const invalidWeb = analytics();
  invalidWeb.data.viewer.accounts[0].webUsage[0].sum.egressBytes = -1;
  for (const data of [missingWeb, invalidWeb, { errors: [{ message: "unreadable" }] }]) {
    resetWorkerState();
    const deps = upstream(data);
    const response = await handleRequest(request("/turn-credentials", token()), env, deps);
    assert.equal(response.status, 503);
    assert.equal((await response.json()).error, "unreadable");
    assert.equal(deps.calls.length, 1);
  }
});

test("web credentials use only the web key, TTL <= one hour, and no-store", async () => {
  resetWorkerState();
  const deps = upstream(analytics(1, 2), 86400);
  const response = await handleRequest(request("/turn-credentials", token()), { ...env, TURN_CREDENTIAL_TTL_SECONDS: "86400" }, deps);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Cache-Control"), "no-store");
  assert.equal((await response.json()).ttl, 3600);
  assert.equal(deps.calls.length, 2);
  assert.equal(deps.calls[0].body.variables.keyId, "desktop-key");
  assert.equal(deps.calls[0].body.variables.webKeyId, "web-key");
  assert.match(deps.calls[0].body.query, /webUsage: callsTurnUsageAdaptiveGroups/);
  assert.match(deps.calls[1].url, /\/keys\/web-key\/credentials\//);
  assert.equal(deps.calls[1].body.ttl, 3600);
  assert.equal(deps.calls[1].init.headers.authorization, "Bearer web-test-token");
});

test("web daily scope combines both routes and still charges original route caps", async () => {
  resetWorkerState();
  const config = { ...env, ENVIRONMENT: "development", MAILBOX_QUOTA: undefined, WEB_DAILY_REQUESTS: "2" };
  const deps = { ...upstream(), mailboxFetch: async () => new Response(null, { status: 204 }) };
  const get = () => request(`/mailbox/${ID}/offer?client=web`, undefined, null, "GET");
  assert.equal((await handleRequest(get(), config, deps)).status, 204);
  assert.equal((await handleRequest(request("/turn-credentials", token(), null), config, deps)).status, 200);
  const capped = await handleRequest(get(), config, deps);
  assert.equal(capped.status, 429);
  assert.equal((await capped.json()).error, "cap");
  // Desktop remains on its existing scope, without the browser sub-cap.
  assert.equal((await handleRequest(request(`/mailbox/${ID}/offer`, undefined, null, "GET"), config, deps)).status, 204);

  resetWorkerState();
  const global = { ...env, ENVIRONMENT: "development", MAILBOX_QUOTA: undefined, MAILBOX_DAILY_REQUESTS: "1" };
  assert.equal((await handleRequest(get(), global, deps)).status, 204);
  assert.equal((await handleRequest(request(`/mailbox/${ID}/offer`, undefined, null, "GET"), global, deps)).status, 429);
});

test("configured web caps cannot exceed free safety ceilings", () => {
  assert.equal(monthlyCapBytes("1000"), 800e9);
  assert.equal(webMonthlyCapBytes("1000"), 200e9);
  assert.equal(webMonthlyCapBytes(undefined), 200e9);
  assert.equal(quotaLimits({ WEB_DAILY_REQUESTS: "100000" }, "web").dailyRequests, 3000);
  assert.equal(credentialTtlSeconds("7200", true), 3600);
});

test("web credentials explicitly identify web quota accounting and pin the provider relay IPs", async () => {
  resetWorkerState();
  const response = await handleRequest(request("/turn-credentials", token()), env, upstream());
  assert.equal(response.status, 200);
  const document = await response.json();
  assert.equal(document.client, "web");
  assert.deepEqual(document.allowed_relay_ips, ["141.101.90.1", "162.159.207.1"]);
  assert.equal(document.quota_bytes, 256 * 1024 * 1024);
  assert.equal(JSON.stringify(document).includes("web-test-token"), false);
});

test("usage cache cannot cross account/key identities and expired data cannot mask analytics failure", async () => {
  resetWorkerState();
  assert.equal((await handleRequest(request("/turn-credentials", token()), env, upstream())).status, 200);
  const other = upstream({ errors: [{ message: "offline" }] });
  assert.equal((await handleRequest(request("/turn-credentials", token()), { ...env, WEB_TURN_KEY_ID: "another-key" }, other)).status, 503);
  assert.equal(other.calls.length, 1);
  const stale = { ...upstream({ errors: [{ message: "offline" }] }), nowMs: NOW + 60_001 };
  assert.equal((await handleRequest(request("/turn-credentials", token()), env, stale)).status, 503);
});

function multiScopeQuotaEnv(quotaEnv = {}) {
  const objects = new Map();
  const memories = new Map();
  return {
    memories,
    env: {
      ...quotaEnv,
      MAILBOX_QUOTA: {
        idFromName: (scope) => scope,
        get(scope) {
          if (!objects.has(scope)) {
            const memory = memoryQuotaStorage();
            memories.set(scope, memory);
            objects.set(scope, new MailboxQuota({ storage: memory.storage }, quotaEnv));
          }
          return objects.get(scope);
        },
      },
    },
  };
}

test("web POST and DELETE mailbox requests charge mailbox row writes to both mailbox and web scopes", async () => {
  const calls = [];
  const config = {
    ...env,
    ENVIRONMENT: "development",
    MAILBOX_QUOTA: {
      idFromName: (scope) => scope,
      get: (scope) => ({
        fetch: async (request) => {
          calls.push({
            scope,
            writes: request.headers.get("X-WebJam-Row-Writes"),
          });
          return new Response(null, { status: 200 });
        },
      }),
    },
  };
  const deps = {
    nowMs: NOW,
    mailboxFetch: async (request) => new Response(null, {
      status: request.method === "POST" ? 201 : 204,
    }),
  };
  const headers = { "content-type": "application/json", "CF-Connecting-IP": "203.0.113.10" };
  for (const [method, writes] of [["POST", "2"], ["DELETE", "1"]]) {
    resetWorkerState();
    calls.length = 0;
    const init = {
      method,
      headers,
      ...(method === "POST" ? { body: JSON.stringify({ payload: PAYLOAD }) } : {}),
    };
    const response = await handleRequest(
      new Request(`https://worker.test/mailbox/${ID}/offer?client=web`, init),
      config,
      deps,
    );
    assert.equal(response.status, method === "POST" ? 201 : 204);
    assert.deepEqual(
      calls.filter((entry) => entry.scope === "web").map((entry) => entry.writes),
      [writes],
    );
    assert.deepEqual(
      calls.filter((entry) => entry.scope === "mailbox").map((entry) => entry.writes),
      [writes],
    );
  }
});

test("web scope per-minute rate limit interaction between mailbox polling and turn-credentials", async () => {
  resetWorkerState();
  const quotaConfig = {
    MAILBOX_DAILY_REQUESTS: "1000",
    TURN_RATE_LIMIT_PER_MINUTE: "10",
    MAILBOX_RATE_LIMIT_PER_MINUTE: "30",
  };
  const { env: quotaEnv } = multiScopeQuotaEnv(quotaConfig);
  const config = { ...env, ...quotaEnv, ENVIRONMENT: "development" };
  const deps = {
    ...upstream(),
    nowMs: NOW,
    mailboxFetch: async () => new Response(null, { status: 204 }),
  };
  const mailboxGet = () => request(`/mailbox/${ID}/offer?client=web`, undefined, null, "GET");
  for (let index = 0; index < 25; index += 1) {
    assert.equal((await handleRequest(mailboxGet(), config, deps)).status, 204);
  }
  for (let index = 0; index < 5; index += 1) {
    assert.equal((await handleRequest(request("/turn-credentials", token(), null), config, deps)).status, 200);
  }
  const blocked = await handleRequest(request("/turn-credentials", token(), null), config, deps);
  assert.equal(blocked.status, 429);
  assert.deepEqual(await blocked.json(), { error: "rate" });
});

test("desktop turn-credentials behavior when WEB_TURN_KEY_ID is unset vs misconfigured", async () => {
  const desktopBody = { session_id: SESSION, invite_token: INVITE };
  const desktopRequest = () => new Request("https://worker.test/turn-credentials", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "CF-Connecting-IP": "203.0.113.10",
    },
    body: JSON.stringify(desktopBody),
  });
  const desktopOnly = {
    TURN_KEY_ID: "desktop-key",
    TURN_KEY_API_TOKEN: "desktop-test-token",
    CLOUDFLARE_ACCOUNT_ID: "account",
    CLOUDFLARE_ANALYTICS_API_TOKEN: "analytics-test-token",
    MAILBOX_QUOTA: quotaBinding(),
  };
  resetWorkerState();
  assert.equal((await handleRequest(desktopRequest(), desktopOnly, upstream())).status, 200);
  resetWorkerState();
  const misconfigured = { ...desktopOnly, WEB_TURN_KEY_ID: "desktop-key" };
  const response = await handleRequest(desktopRequest(), misconfigured, upstream());
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: "misconfigured" });
});
