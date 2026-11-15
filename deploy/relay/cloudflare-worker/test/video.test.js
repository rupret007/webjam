import test from "node:test";
import assert from "node:assert/strict";
import { applyMailbox, applyVideoMailbox, emptyMailbox } from "../src/mailbox.js";
import { handleRequest, resetWorkerState } from "../src/worker.js";

const now = 1700000000000;
const offer = { kind: "put", slot: "offer", payload: "sealed_offer" };
const answer = { kind: "put", slot: "answer", payload: "sealed_answer" };

test("video writes extend only the independent video alarm", () => {
  const music = emptyMailbox();
  assert.equal(applyMailbox(music, offer, now).status, 201);
  assert.equal(applyMailbox(music, answer, now + 510000).status, 201);
  assert.equal(music.createdAt + 600000, now + 600000);
  const video = emptyMailbox();
  assert.equal(applyVideoMailbox(video, offer, now + 510000).status, 201);
  assert.equal(video.alarmAt, now + 1110000);
  assert.equal(applyVideoMailbox(video, offer, now + 515000).status, 409);
  assert.equal(video.alarmAt, now + 1110000);
  assert.equal(applyVideoMailbox(video, answer, now + 520000).status, 201);
  assert.equal(video.alarmAt, now + 1120000);
  assert.equal(applyVideoMailbox(video, {kind: "get", slot: "offer"}, video.alarmAt).status, 410);
});

test("video signaling uses its own binding and cannot delete or upgrade", async () => {
  const binding = label => ({ idFromName: id => id, get: () => ({ fetch: () => new Response(label) }) });
  const env = { MAILBOX: binding("music"), VIDEO_MAILBOX: binding("video") };
  const deps = { enforceAccess: () => null };
  const request = (route, init) => new Request("https://relay.example/" + route + "/" + "a".repeat(64) + "/offer", init);
  assert.equal(await (await handleRequest(request("mailbox"), env, deps)).text(), "music");
  assert.equal(await (await handleRequest(request("video-mailbox"), env, deps)).text(), "video");
  assert.equal((await handleRequest(request("video-mailbox", {method:"DELETE"}), env, deps)).status, 405);
  assert.equal((await handleRequest(request("video-mailbox", {headers:{Upgrade:"websocket"}}), env, deps)).status, 405);
});

test("a video request cannot fall back to either music key, even without Origin", async () => {
  for (const video of [undefined, "web", "desktop"]) {
    for (const origin of [undefined,"https://stalemate.band"]) {
      resetWorkerState();
      const request = new Request("https://relay.example/turn-credentials?client=web-video", {
        method:"POST", headers:{"content-type":"application/json", "CF-Connecting-IP":"203.0.113.8", ...(origin ? {origin} : {})},
        body:JSON.stringify({client:"web-video", session_id:"1".repeat(64), invite_token:"a".repeat(43)}),
      });
      const response = await handleRequest(request, {
        ENVIRONMENT:"development", TURN_KEY_ID:"desktop", WEB_TURN_KEY_ID:"web",
        VIDEO_TURN_KEY_ID:video, VIDEO_TURN_KEY_API_TOKEN:"fixture", VIDEO_TURN_MONTHLY_CAP_BYTES:"1000",
        CLOUDFLARE_ACCOUNT_ID:"fixture", CLOUDFLARE_ANALYTICS_API_TOKEN:"fixture",
      }, { nowMs:now, enforceAccess:()=>null, fetch:()=>assert.fail("unexpected provider call") });
      assert.equal(response.status, 503);
    }
  }
});

test("video charges a separate budget and requires an explicit signaling limit", async () => {
  resetWorkerState();
  const env = { ENVIRONMENT: "development", VIDEO_MAILBOX: {}, MAILBOX: {} };
  const deps = { nowMs: now, mailboxFetch: () => new Response(null, { status: 204 }) };
  const request = route => new Request("https://relay.example/" + route + "/" + "a".repeat(64) + "/offer",
    { headers: {"CF-Connecting-IP": "203.0.113.8"} });
  assert.equal((await handleRequest(request("video-mailbox"), env, deps)).status, 503);
  env.VIDEO_SIGNAL_DAILY_REQUESTS = "1";
  assert.equal((await handleRequest(request("video-mailbox"), env, deps)).status, 204);
  assert.equal((await handleRequest(request("video-mailbox"), env, deps)).status, 429);
  assert.equal((await handleRequest(request("mailbox"), env, deps)).status, 204);
});

test("a configured video key requires active admission before contacting Cloudflare", async () => {
  const session = "1".repeat(64);
  const invite = "a".repeat(43);
  const config = {
    ENVIRONMENT: "development", TURN_KEY_ID: "desktop", WEB_TURN_KEY_ID: "web",
    VIDEO_TURN_KEY_ID: "video", VIDEO_TURN_KEY_API_TOKEN: "fixture", VIDEO_TURN_MONTHLY_CAP_BYTES: "1000",
    CLOUDFLARE_ACCOUNT_ID: "fixture", CLOUDFLARE_ANALYTICS_API_TOKEN: "fixture",
  };
  const request = () => new Request("https://relay.example/turn-credentials?client=web-video", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ client: "web-video", session_id: session, invite_token: invite }),
  });
  const upstream = [];
  const deps = {
    nowMs: now, enforceAccess: () => null,
    fetch(url) {
      upstream.push(url);
      if (url.endsWith("graphql")) {
        return Response.json({ data: { viewer: { accounts: [{ callsTurnUsageAdaptiveGroups: [] }] } } });
      }
      return Response.json({ iceServers: [{ urls: "turn:relay.example:3478" }] });
    },
  };
  const call = env => handleRequest(request(), env, deps);
  assert.equal((await call(config)).status, 503);

  let status = 403;
  let checks = 0;
  const VIDEO_ADMISSION = {
    async fetch(req) {
      checks++;
      assert.equal(req.method, "POST");
      assert.equal(new URL(req.url).pathname, "/v1/check");
      assert.equal(req.headers.has("Authorization"), false);
      const proof = await req.json();
      assert.deepEqual(proof, {
        session_id: session,
        invite_digest: "66d34fba71f8f450f7e45598853e53bfc23bbd129027cbb131a2f4ffd7878cd0",
      });
      return new Response(null, { status });
    },
  };
  const env = { ...config, VIDEO_ADMISSION };
  assert.equal((await call(env)).status, 403);
  status = 200;
  assert.equal((await call(env)).status, 503);
  const unavailable = { fetch() { throw new Error("Admission service unavailable"); } };
  assert.equal((await call({ ...env, VIDEO_ADMISSION: unavailable })).status, 503);
  assert.equal(upstream.length, 0);

  status = 204;
  const response = await call(env);
  assert.equal(response.status, 200);
  const credentials = await response.json();
  assert.equal(credentials.client, "web-video");
  assert.equal(credentials.quota_bytes, 64 * 1024 * 1024);
  assert.equal(upstream.length, 2);
  assert.match(upstream[1], /keys\/video\/credentials/);

  // A second request also checks admission. The authority can revoke a room
  // between the first issuance and renewal.
  status = 403;
  assert.equal((await call(env)).status, 403);
  assert.equal(upstream.length, 2);
  assert.equal(checks, 4);
});

test("video admission timeout and redirect fail closed before a provider call", async t => {
  const config = {
    ENVIRONMENT: "development", TURN_KEY_ID: "desktop", WEB_TURN_KEY_ID: "web",
    VIDEO_TURN_KEY_ID: "video", VIDEO_TURN_KEY_API_TOKEN: "fixture", VIDEO_TURN_MONTHLY_CAP_BYTES: "1000",
    CLOUDFLARE_ACCOUNT_ID: "fixture", CLOUDFLARE_ANALYTICS_API_TOKEN: "fixture",
  };
  const request = () => new Request("https://relay.example/turn-credentials?client=web-video", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ client: "web-video", session_id: "1".repeat(64), invite_token: "a".repeat(43) }),
  });
  const originalClear = globalThis.clearTimeout;
  let cleared = 0;
  t.mock.method(globalThis, "clearTimeout", timer => { cleared++; return originalClear(timer); });
  const deps = { nowMs: now, enforceAccess: () => null, fetch: () => assert.fail("provider contacted"), fetchTimeoutMs: 5 };
  let aborted = false;
  const hanging = { fetch(req) { return new Promise((_, reject) => {
    req.signal.addEventListener("abort", () => { aborted = true; reject(new Error("aborted")); }, { once: true });
  }); } };
  assert.equal((await handleRequest(request(), { ...config, VIDEO_ADMISSION: hanging }, deps)).status, 503);
  assert.equal(aborted, true);
  assert.ok(cleared >= 1);

  const beforeRedirect = cleared;
  const redirecting = { fetch(req) {
    assert.equal(req.redirect, "error");
    return Response.redirect("https://other.example/v1/check");
  } };
  assert.equal((await handleRequest(request(), { ...config, VIDEO_ADMISSION: redirecting }, deps)).status, 503);
  assert.ok(cleared > beforeRedirect);
});
