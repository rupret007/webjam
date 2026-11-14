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
