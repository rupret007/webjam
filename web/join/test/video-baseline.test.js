import test from "node:test";
import assert from "node:assert/strict";
import { WebJoinSession } from "../session.js";
import { musicBaseline } from "../video-guard.js";

// 40 ms RTT on "fast" gives a 50 ms music estimate; 62 ms gives 61 ms.
function musicStats() {
  const pair = { type: "candidate-pair", state: "succeeded", nominated: true, currentRoundTripTime: 0.04 };
  return new Map([
    ["transport", { type: "transport", selectedCandidatePairId: "fast", bytesSent: 5, bytesReceived: 5 }],
    ["fast", { ...pair, localCandidateId: "direct", remoteCandidateId: "host" }],
    ["other", { ...pair, localCandidateId: "direct", remoteCandidateId: "host" }],
    ["first-relay", { ...pair, localCandidateId: "relay", remoteCandidateId: "host" }],
    ["direct", { type: "local-candidate", candidateType: "host" }],
    ["relay", { type: "local-candidate", candidateType: "relay" }],
    ["host", { type: "remote-candidate", candidateType: "host" }],
    ["audio", { type: "inbound-rtp", kind: "audio", packetsReceived: 100, packetsLost: 0, jitter: 0,
      jitterBufferDelay: 2, jitterBufferEmittedCount: 100 }],
  ]);
}

function musicSession(t, stats, options = {}) {
  t.mock.timers.enable({ apis: ["Date"], now: 1000000 });
  const track = { enabled: true, stop() {} };
  const session = new WebJoinSession({ origin: "https://relay.example", invite: {},
    stream: { getAudioTracks: () => [track], getTracks: () => [track] },
    audioContext: { baseLatency: 0.01 }, onError: error => assert.fail(error), ...options });
  session.pc = { connectionState: "connected", getStats: async () => stats, close() {} };
  session.authorized = true;
  t.after(() => session.close());
  return session;
}

async function seconds(t, session, count) {
  for (let i = 0; i < count; i++) {
    await session.updateStats();
    t.mock.timers.tick(1000);
  }
}

test("a music baseline still forms when timing was unmeasurable for the first 30 seconds", async t => {
  const stats = musicStats();
  const session = musicSession(t, stats);
  const transport = stats.get("transport");
  transport.selectedCandidatePairId = "missing";
  await seconds(t, session, 45);
  assert.equal(session.videoBaseline, undefined);

  transport.selectedCandidatePairId = "fast";
  await seconds(t, session, 10);
  // Samples older than the 30 s window do not count toward the baseline.
  transport.selectedCandidatePairId = "missing";
  await seconds(t, session, 25);
  transport.selectedCandidatePairId = "fast";
  await seconds(t, session, 19);
  assert.equal(session.videoBaseline, undefined);
  await seconds(t, session, 1);
  assert.equal(session.videoBaseline.latency, 50);
  assert.equal(session.videoBaseline.pairId, "fast");
});

test("the baseline path is the most common pair, so a later move onto relay still trips video", async t => {
  const stats = musicStats();
  let camera = null;
  let trips = 0;
  const session = musicSession(t, stats, { videoPeer: () => camera, onVideoTrip() { trips++; } });
  const transport = stats.get("transport");
  await seconds(t, session, 19);
  // A single sample from a relay pair at the end must not define the path.
  transport.selectedCandidatePairId = "first-relay";
  await seconds(t, session, 1);
  assert.equal(session.videoBaseline.pairId, "fast");
  assert.equal(session.videoBaseline.route, "direct");

  camera = { connectionState: "connected" };
  transport.selectedCandidatePairId = "fast";
  await seconds(t, session, 2);
  assert.equal(trips, 0);
  transport.selectedCandidatePairId = "first-relay";
  await seconds(t, session, 1);
  assert.equal(trips, 1);
});

test("after a timing trip, video is offered again only once fresh music timing is healthy", async t => {
  const stats = musicStats();
  let camera = null;
  let trips = 0;
  const session = musicSession(t, stats, { videoPeer: () => camera, onVideoTrip() { trips++; camera = null; } });
  await seconds(t, session, 20);
  assert.equal(session.videoBaseline.latency, 50);

  camera = { connectionState: "connected" };
  stats.get("fast").currentRoundTripTime = 0.062;
  await seconds(t, session, 5);
  assert.equal(trips, 1);
  assert.equal(session.videoBaseline, null);

  // Music stays 11 ms slower than before video: not healthy, keep waiting.
  await seconds(t, session, 40);
  assert.equal(session.videoBaseline, null);

  stats.get("fast").currentRoundTripTime = 0.04;
  await seconds(t, session, 9);
  assert.equal(session.videoBaseline, null, "most of the window is still slow");
  await seconds(t, session, 1);
  assert.equal(session.videoBaseline.latency, 50);
  assert.equal(session.authorized, true);
});

test("a recovered baseline keeps the first trip limits", () => {
  const sample = (latency, targetMs, pairId, route) => ({ latency, targetMs, pairId, route, rttMs: 40, received: 100, loss: 0 });
  const reference = musicBaseline(Array.from({ length: 20 }, () => sample(50, 20, "fast", "direct")));
  assert.deepEqual([reference.latency, reference.targetMs, reference.pairId, reference.route], [50, 20, "fast", "direct"]);

  assert.equal(musicBaseline(Array.from({ length: 20 }, () => sample(61, 20, "fast", "direct")), reference), null);
  assert.equal(musicBaseline(Array.from({ length: 20 }, () => sample(50, 30, "fast", "direct")), reference), null);
  const recovered = musicBaseline(Array.from({ length: 20 }, () => sample(55, 20, "relay", "relayed")), reference);
  // The path follows the fresh window; the limits stay where music started.
  assert.deepEqual([recovered.latency, recovered.targetMs, recovered.pairId, recovered.route], [50, 20, "relay", "relayed"]);
});
