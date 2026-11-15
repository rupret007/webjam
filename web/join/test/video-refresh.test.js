import test from "node:test";
import assert from "node:assert/strict";
import { setImmediate } from "node:timers/promises";
import { VideoConflict } from "../video-mailbox.js";
import { VideoRefresh } from "../video-refresh.js";

const clockStart = 100000;
const expiresAt = clockStart + 90000;
const previous = { expiresAt, iceServers: [{ urls: "previous" }] };
const next = { expiresAt: clockStart + 600000, iceServers: [{ urls: "next" }] };
const sdp = [
  "v=0", "m=video 9 UDP/TLS/RTP/SAVPF 96", "a=sendrecv", "a=rtpmap:96 VP8/90000",
  "a=fingerprint:sha-256 " + Array(32).fill("A1").join(":"),
  "a=candidate:1 1 udp 100 8.8.8.8 10500 typ srflx", "a=end-of-candidates",
].join("\r\n");

function fixture(t, { host = false, failCredentials = 0 } = {}) {
  t.mock.timers.enable({ apis: ["Date", "setTimeout", "setInterval"], now: clockStart });
  const events = [];
  const stored = new Map();
  let servers = previous.iceServers;
  let postCount = 0;
  const peer = Object.assign(new EventTarget(), {
    connectionState: "connected",
    signalingState: "stable",
    iceGatheringState: "complete",
    async getStats() {
      return new Map([
        ["transport", { type: "transport", selectedCandidatePairId: "pair" }],
        ["pair", { type: "candidate-pair", nominated: true, state: "succeeded", localCandidateId: "local", remoteCandidateId: "remote" }],
        ["local", { candidateType: "relay" }], ["remote", { candidateType: "srflx" }],
      ]);
    },
    getConfiguration: () => ({ iceServers: servers }),
    setConfiguration: ({ iceServers }) => {
      servers = iceServers;
      events.push(iceServers === previous.iceServers ? "restore" : "configure");
    },
    async createOffer(options) {
      assert.deepEqual(options, { iceRestart: true });
      events.push("createOffer");
      return { type: "offer", sdp };
    },
    async setLocalDescription(description) {
      this.localDescription = description;
      this.signalingState = description.type === "offer" ? "have-local-offer" : "stable";
      events.push(description.type);
    },
    setRemoteDescription: () => assert.fail("unexpected answer"),
  });
  const mailbox = {
    read: async (round, kind) => stored.get(`${round}/${kind}`),
    write: async (round, kind, value) => {
      events.push(round);
      stored.set(`${round}/${kind}`, value);
    },
    credentials: async () => {
      events.push("credentials");
      if (++postCount <= failCredentials) throw new Error("temporary issuer error");
      return next;
    },
  };
  const controller = new AbortController();
  const refresh = new VideoRefresh({ peer, mailbox, credentials: previous, host, signal: controller.signal,
    onExpired: () => events.push("expired"),
  });
  t.after(() => { controller.abort(); refresh.close(); });
  async function advance(milliseconds) {
    t.mock.timers.tick(milliseconds);
    await setImmediate();
  }
  return { refresh, peer, events, stored, advance };
}

test("a failed issuer request retries in the same generation and spends the window retry", async t => {
  const { refresh, peer, events, stored, advance } = fixture(t, { failCredentials: 1 });
  const running = refresh.run();
  await advance(0);
  assert.deepEqual(events, ["video-refresh-gen-1", "credentials"]);
  await advance(14999);
  assert.equal(events.length, 2);
  await advance(1);
  assert.deepEqual(events, [
    "video-refresh-gen-1", "credentials", "credentials", "configure", "createOffer", "offer", "video-refresh-1",
  ]);
  await advance(15000);
  await running;
  assert.deepEqual(events.slice(7), ["rollback", "restore"]);
  assert.equal(peer.signalingState, "stable");
  assert.equal(peer.connectionState, "connected");
  assert.deepEqual(peer.getConfiguration().iceServers, previous.iceServers);
  assert.equal(refresh.credentials, previous);
  assert.equal(stored.has("video-refresh-gen-2/offer"), false);
});

test("a missing answer spends the single retry on a fresh generation", async t => {
  const { refresh, peer, events, stored, advance } = fixture(t);
  const running = refresh.run();
  await advance(0);
  assert.equal(stored.has("video-refresh-1/offer"), true);
  await advance(15000);
  assert.equal(stored.has("video-refresh-2/offer"), true);
  stored.set("video-refresh-1/answer", { sdp });
  await advance(15000);
  await running;
  assert.equal(stored.has("video-refresh-gen-3/offer"), false);
  assert.equal(events.filter(event => event === "rollback").length, 2);
  assert.equal(events.filter(event => event === "credentials").length, 2);
  assert.equal(peer.signalingState, "stable");
  assert.equal(peer.connectionState, "connected");
});

test("a relay host seeing only the generation marker never requests credentials", async t => {
  const { refresh, events, stored, advance } = fixture(t, { host: true });
  stored.set("video-refresh-gen-1/offer", { v: 1, gen: 1 });
  const running = refresh.run();
  await advance(0);
  await advance(74999);
  await advance(1);
  await running;
  assert.deepEqual(events, []);
});

test("a short-lived direct replacement keeps expiry without reissuing credentials immediately", async t => {
  const { refresh, peer, events, advance } = fixture(t);
  const getStats = peer.getStats;
  peer.getStats = async () => {
    const stats = await getStats();
    stats.get("local").candidateType = "host";
    return stats;
  };
  refresh.mailbox.credentials = async () => {
    events.push("credentials");
    return { ...next, expiresAt: clockStart + 60000 };
  };
  refresh.start();
  await advance(0);
  await advance(1000);
  assert.deepEqual(events, ["credentials", "configure"]);
  assert.equal(refresh.refreshTimer, null);
  await advance(59000);
  assert.deepEqual(events, ["credentials", "configure"]);
});

test("a restart on the same relay stays live until the previous credential expires", async t => {
  const { refresh, peer, events, advance } = fixture(t);
  refresh.arm();
  peer.setConfiguration({ iceServers: next.iceServers });
  const checking = refresh.connected(1, next, { id: "pair", route: "relayed" });
  await advance(0);
  await advance(15000);
  await checking;
  assert.deepEqual(events, ["configure", "restore"]);
  assert.equal(refresh.credentials, previous);
  assert.equal(refresh.completed, 0);
  await advance(74999);
  assert.deepEqual(events, ["configure", "restore"]);
  await advance(1);
  assert.deepEqual(events, ["configure", "restore", "expired"]);
});

test("an earlier expiry check cannot pause a successfully renewed video pair", async t => {
  const { refresh, peer, events, advance } = fixture(t);
  const priorStats = await peer.getStats();
  const expiryStats = Promise.withResolvers();
  refresh.arm();
  peer.getStats = () => expiryStats.promise;
  await advance(90000);

  const renewedStats = new Map(priorStats);
  renewedStats.set("transport", { ...priorStats.get("transport"), selectedCandidatePairId: "renewed" });
  renewedStats.set("renewed", priorStats.get("pair"));
  peer.getStats = async () => renewedStats;
  await refresh.connected(1, next, { id: "pair", route: "relayed" });
  assert.equal(refresh.credentials, next);

  expiryStats.resolve(priorStats);
  await advance(0);
  assert.deepEqual(events, []);
  refresh.stopRenewing();
  await advance(next.expiresAt - Date.now());
  assert.deepEqual(events, ["expired"]);
});

test("a conflicting refresh stops both peers without creating another generation", async t => {
  const { refresh, peer, events, stored } = fixture(t);
  const mailbox = refresh.mailbox;
  const write = mailbox.write;
  mailbox.write = async (round, kind, value) => {
    if (round === "video-refresh-1") throw new VideoConflict("conflict");
    await write(round, kind, value);
  };
  mailbox.abortRefresh = () => mailbox.write("video-refresh-abort", "offer", { v: 1, abort: "refresh" });
  const host = new VideoRefresh({ peer, mailbox, credentials: previous, host: true,
    signal: refresh.signal, onExpired: () => assert.fail("unexpected host expiry"),
  });
  t.after(() => host.close());
  await refresh.run();
  assert.equal(refresh.stopped, true);
  assert.equal(peer.signalingState, "stable");
  assert.deepEqual(peer.getConfiguration().iceServers, previous.iceServers);
  await host.run();
  assert.equal(host.stopped, true);
  await refresh.run();
  assert.equal(stored.has("video-refresh-gen-2/offer"), false);
  assert.equal(events.filter(event => event === "video-refresh-abort").length, 1);
  assert.equal(events.filter(event => event === "credentials").length, 1);
});

test("the guest stops after generation six without a new credential request", async t => {
  const { refresh, events, stored } = fixture(t);
  for (let gen = 1; gen <= 6; gen++) {
    stored.set(`video-refresh-gen-${gen}/offer`, { v: 1, gen });
  }
  await refresh.run();
  assert.equal(refresh.latestGeneration, 6);
  assert.deepEqual(events, []);
});
