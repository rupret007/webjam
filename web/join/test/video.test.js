import test from "node:test";
import assert from "node:assert/strict";
import { mailboxId } from "../crypto.js";
import { musicVideoTrip, videoRelayBudget } from "../video-guard.js";
import { validateCredentials, validateVideoCredentials, VIDEO_BYTES } from "../policy.js";
import { gatherVideo } from "../video-peer.js";
import { VideoConflict, VideoMailbox } from "../video-mailbox.js";

const pc = { connectionState: "connected", signalingState: "stable" };
const base = { latency: 40, rttMs: 40, pairId: "first", loss: 0.02, targetMs: 30 };
const trip = (change, samples = []) => musicVideoTrip(pc, () => ({ ...base, route: "direct", received: 50, ...change }), base, samples);

test("video trips on music degradation and allows a benign pair change", () => {
  assert.equal(trip({}, [50, 50, 50, 50, 50]), false);
  assert.equal(trip({}, [51, 51, 51, 51, 51]), true);
  assert.equal(trip({pairId:"second", rttMs:50}), false);
  assert.equal(trip({pairId:"second", rttMs:51}), true);
  assert.equal(trip({pairId:"second", route:"relayed"}), true);
  assert.equal(trip({loss:0.029}), false);
  assert.equal(trip({loss:0.031}), true);
  assert.equal(trip({loss:0.031, received:49}), false);
  assert.equal(trip({targetMs:31}), true);
  for (const closed of [null, {signalingState:"closed"}, {connectionState:"closed"}]) {
    assert.equal(musicVideoTrip(closed, () => assert.fail("stats read"), base, []), false);
  }
});

test("video charges bytes only when the route is confirmed", () => {
  const state = { counted: 0, watermark: 0 };
  const tick = (bytes, nominated, route) => videoRelayBudget(new Map([
    ["transport", { type:"transport", selectedCandidatePairId:"pair", bytesSent:bytes, bytesReceived:0 }],
    ["pair", { type:"candidate-pair", state:nominated ? "succeeded" : "in-progress", nominated, localCandidateId:"local", remoteCandidateId:"remote" }],
    ["local", {candidateType:route}], ["remote", {candidateType:"srflx"}],
  ]), state);
  tick(5, false, "relay"); tick(10, false, "relay");
  assert.deepEqual(state, { counted:0, watermark:0 });
  tick(15, true, "relay"); tick(15, true, "relay");
  assert.deepEqual(state, { counted:15, watermark:15 });
  tick(21, true, "host"); tick(24, true, "relay");
  assert.deepEqual(state, { counted:18, watermark:24 });
});

test("video uses separate rounds and credentials", async () => {
  const invite = { capability: new Uint8Array(32).fill(7), session:"1".repeat(64) };
  assert.notEqual(await mailboxId(invite,"offer",0), await mailboxId(invite,"offer",0,"video"));
  assert.notEqual(await mailboxId(invite,"offer",0,"video"), await mailboxId(invite,"offer",0,"video-call-2"));
  await mailboxId(invite,"offer",0,"video-call-4");
  await mailboxId(invite,"offer",0,"call-2-video-refresh-gen-1");
  await mailboxId(invite,"offer",0,"call-4-video-refresh-abort");
  await assert.rejects(mailboxId(invite,"offer",0,"video2"));
  await assert.rejects(mailboxId(invite,"offer",0,"video-refresh-7"));
  await assert.rejects(mailboxId(invite,"offer",0,"video-call-1"));
  await assert.rejects(mailboxId(invite,"offer",0,"video-call-5"));
  await assert.rejects(mailboxId(invite,"offer",0,"call-1-video-refresh-1"));
  await assert.rejects(mailboxId(invite,"offer",0,"call-5-video-refresh-1"));
  const doc = { client:"web-video", ttl:600, quota_bytes:VIDEO_BYTES,
    iceServers:[{urls:"turn:turn.cloudflare.com:3478?transport=udp", username:"fixture", credential:"fixture"}] };
  assert.equal(validateVideoCredentials(doc, 0).expiresAt, 600000);
  assert.throws(() => validateCredentials(doc));
  assert.throws(() => validateVideoCredentials({...doc, iceServers:[{urls:"stun:stun.cloudflare.com:3478"}]}));
});

test("a duplicate video slot requires identical sealed bytes", async () => {
  const invite = { capability: new Uint8Array(32).fill(7), session: "1".repeat(64) };
  const mailbox = new VideoMailbox("https://relay.example", invite);
  const slots = new Map();
  mailbox.request = async (path, options) => {
    if (!options) return { status: 200, body: { payload: slots.get(path) } };
    if (slots.has(path)) return { status: 409 };
    slots.set(path, JSON.parse(options.body).payload);
    return { status: 201 };
  };
  const round = "video-refresh-gen-1";
  const marker = { v: 1, gen: 1 };
  // Replay the exact same sealed request, then a fresh encryption of the same data.
  const nonce = new Uint8Array(12).fill(2);
  await mailbox.write(round, "offer", marker, nonce);
  await mailbox.write(round, "offer", marker, nonce);
  await assert.rejects(mailbox.write(round, "offer", marker, new Uint8Array(12).fill(3)), VideoConflict);
  assert.deepEqual(await mailbox.read(round, "offer"), marker);
});

test("video waits for complete vanilla gathering and retains the end marker", async () => {
  const peer = new EventTarget();
  peer.iceGatheringState = "gathering";
  peer.localDescription = { sdp: [
    "v=0","m=video 9 UDP/TLS/RTP/SAVPF 96","a=sendrecv","a=rtpmap:96 VP8/90000",
    "a=ice-options:trickle", "a=fingerprint:sha-256 " + Array(32).fill("A1").join(":"),
    "a=candidate:1 1 udp 100 8.8.8.8 10500 typ srflx","a=end-of-candidates",
  ].join("\r\n") };
  let finished = false;
  const pending = gatherVideo(peer,new AbortController().signal).then(value => { finished = true; return value; });
  peer.dispatchEvent(new Event("icecandidate"));
  await Promise.resolve();
  assert.equal(finished,false);
  peer.iceGatheringState = "complete";
  peer.dispatchEvent(new Event("icegatheringstatechange"));
  const {sdp} = await pending;
  assert.match(sdp,/a=end-of-candidates/);
  assert.doesNotMatch(sdp,/trickle/);
});

test("direct video renews 90 seconds before expiry without an ICE restart", async t => {
  const { VideoRefresh } = await import("../video-refresh.js");
  t.mock.timers.enable({ apis: ["Date", "setTimeout", "setInterval"], now: 100000 });
  let renewed;
  const refreshDone = new Promise(resolve => { renewed = resolve; });
  const next = { expiresAt: 710000, iceServers: [] };
  const peer = {
    getStats: async () => new Map([
      ["t", { type:"transport", selectedCandidatePairId:"p" }],
      ["p", { type:"candidate-pair", nominated:true, state:"succeeded", localCandidateId:"l", remoteCandidateId:"r" }],
      ["l", { candidateType:"host" }], ["r", { candidateType:"srflx" }],
    ]),
    getConfiguration: () => ({}),
    setConfiguration: ({ iceServers }) => renewed(iceServers),
    createOffer: () => assert.fail("direct refresh must not create an offer"),
  };
  let requests = 0;
  const mailbox = {
    read: async () => null,
    write: () => assert.fail("direct refresh must not reserve a generation"),
    credentials: async () => { requests++; return next; },
  };
  const refresh = new VideoRefresh({ peer, mailbox, credentials: { expiresAt: 200000 },
    signal: new AbortController().signal, onExpired: () => assert.fail("unexpected expiry") });
  refresh.start();
  try {
    t.mock.timers.tick(9999);
    assert.equal(requests, 0);
    t.mock.timers.tick(1);
    assert.deepEqual(await refreshDone, []);
    assert.equal(requests, 1);
    assert.equal(refresh.credentials, next);
  } finally {
    refresh.close();
  }
});
