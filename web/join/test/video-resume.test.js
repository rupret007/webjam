import test from "node:test";
import assert from "node:assert/strict";
import { setImmediate } from "node:timers/promises";
import { mailboxId, sdpFingerprint } from "../crypto.js";
import { MAX_CAMERA_CALLS, VideoRoom } from "../video-room.js";
import { VideoRefresh } from "../video-refresh.js";

const CAMERA_SDP = [
  "v=0", "m=video 9 UDP/TLS/RTP/SAVPF 96", "a=sendrecv", "a=rtpmap:96 VP8/90000",
  "a=fingerprint:sha-256 " + Array(32).fill("AB").join(":"),
  "a=candidate:1 1 udp 100 8.8.8.8 10500 typ srflx", "a=end-of-candidates",
].join("\r\n");

const TRIP = "Video paused to protect music timing. Music stays connected.";

function element() {
  return { textContent: "", title: "", disabled: false, hidden: false, srcObject: null, removeEventListener() {} };
}

function cameraRoom(t, { ready = () => true } = {}) {
  t.mock.timers.enable({ apis: ["Date", "setTimeout", "setInterval"], now: 1000000 });
  const previousDocument = globalThis.document;
  globalThis.document = { removeEventListener() {} };
  const room = Object.create(VideoRoom.prototype);
  Object.assign(room, {
    root: { dataset: {} }, origin: "https://relay.example", invite: { expiresAt: 1000000 / 1000 + 3600 },
    host: false, timingReady: ready, label: "Show camera", call: 1, relayBudget: { counted: 7, watermark: 3 },
    camera: element(), switcher: element(), preview: element(), remote: element(), placeholder: element(), status: element(),
    credentials: { expiresAt: 1000000 + 600000, iceServers: [] },
  });
  room.startCall();
  const events = [];
  room.peer = { connectionState: "connected", close() { events.push("peer closed"); } };
  room.track = { readyState: "live", enabled: true, stop() { events.push("track stopped"); } };
  room.refresh = { close() { events.push("refresh closed"); } };
  room.render("live", "Cameras on.");
  t.after(() => {
    room.close();
    globalThis.document = previousDocument;
  });
  return { room, events };
}

async function tick(t, milliseconds) {
  t.mock.timers.tick(milliseconds);
  await setImmediate();
}

test("a timing trip ends only this camera call and offers the tap again once music timing is healthy", async t => {
  let healthy = false;
  const { room, events } = cameraRoom(t, { ready: () => healthy });
  const firstSignal = room.signal;
  room.pause(TRIP, { resumable: true });

  assert.deepEqual(events, ["refresh closed", "track stopped", "peer closed"]);
  assert.equal(firstSignal.aborted, true);
  assert.equal(room.signal.aborted, false);
  assert.equal(room.peer, null);
  assert.equal(room.ended, undefined);
  assert.equal(room.round(), "video-call-2");
  assert.equal(room.relayBudget.counted, 7, "relay bytes still count after a restart");
  assert.equal(room.state, "paused");
  assert.equal(room.status.textContent, TRIP);
  assert.equal(room.camera.disabled, true);

  await tick(t, 5000);
  assert.equal(room.state, "paused", "music timing is not healthy yet");
  healthy = true;
  await tick(t, 1000);
  assert.equal(room.state, "needs-gesture");
  assert.equal(room.camera.disabled, false);
  assert.equal(room.camera.textContent, "Show camera");
  assert.match(room.status.textContent, /^Music timing is steady again\. Tap Show camera to restart video\./);
});

test("relay expiry and budget pauses stay final", async t => {
  for (const message of ["Video paused. Video relay expired. Music stays connected.",
    "Video paused. Video relay budget used. Music stays connected."]) {
    const { room } = cameraRoom(t);
    room.pause(message);
    assert.equal(room.ended, true);
    assert.equal(room.signal.aborted, true);
    room.pause(TRIP, { resumable: true });
    await tick(t, 5000);
    assert.equal(room.state, "paused");
    assert.equal(room.status.textContent, message);
    assert.equal(room.camera.disabled, true);
    room.close();
    t.mock.timers.reset();
  }
});

test("camera restarts are bounded per session", t => {
  const { room } = cameraRoom(t);
  room.call = MAX_CAMERA_CALLS;
  room.pause(TRIP, { resumable: true });
  assert.equal(room.ended, true);
  assert.equal(room.status.textContent,
    "Video paused to protect music timing. Camera restarts are used up for this session. Music stays connected.");
});

test("a dropped camera connection can be restarted with a tap", async t => {
  const { room } = cameraRoom(t);
  room.dropped();
  assert.equal(room.state, "paused");
  assert.equal(room.round(), "video-call-2");
  await tick(t, 1000);
  assert.equal(room.state, "needs-gesture");
  assert.match(room.status.textContent, /The camera connection dropped\. Tap Show camera to try again\./);
});

test("a tap before music timing is measurable recovers when the baseline forms", async t => {
  let measurable = false;
  const { room } = cameraRoom(t, { ready: () => measurable });
  room.endCall();
  room.startCall();
  await room.tap();
  assert.equal(room.status.textContent, "Music timing isn't measurable yet. Try again shortly.");
  await tick(t, 3000);
  assert.equal(room.status.textContent, "Music timing isn't measurable yet. Try again shortly.");
  measurable = true;
  await tick(t, 1000);
  assert.equal(room.state, "needs-gesture");
  assert.match(room.status.textContent, /^Music timing is ready\. Tap Show camera to start your camera\./);
});

test("a camera call that fails after a restart cannot pause the next call", async t => {
  const { room } = cameraRoom(t);
  room.endCall();
  room.startCall();
  let fail;
  const track = { readyState: "live", enabled: true, getSettings: () => ({}), addEventListener() {}, stop() {} };
  room.capture = async () => ({ getVideoTracks: () => [track], getTracks: () => [track] });
  room.preview.play = async () => {};
  room.refreshCameraDevices = async () => {};
  room.connect = () => new Promise((_, reject) => { fail = reject; });
  await room.tap();
  assert.equal(room.connecting, true);
  room.pause(TRIP, { resumable: true });
  fail(new Error("Camera signaling unavailable"));
  await setImmediate();
  assert.equal(room.ended, undefined);
  assert.equal(room.status.textContent, TRIP);
});

test("Leave releases video at once without waiting for the peer to report closed", async t => {
  const { room, events } = cameraRoom(t);
  // A peer that never reports "closed" must not hold up music teardown.
  await room.closed();
  assert.deepEqual(events, ["refresh closed", "track stopped", "peer closed"]);
  assert.equal(room.ended, true);
  assert.equal(room.signal.aborted, true);
});

test("an expired invite does not report camera restarts as used up", t => {
  const { room } = cameraRoom(t);
  room.invite.expiresAt = 1;
  room.pause(TRIP, { resumable: true });
  assert.equal(room.ended, true);
  assert.equal(room.call, 1);
  assert.equal(room.status.textContent, TRIP);
});

test("a pause while the host applies the offer does not keep that sender", async t => {
  const { room } = cameraRoom(t);
  const previousPeer = globalThis.RTCPeerConnection;
  const added = [];
  let release;
  let reached;
  const reachedPromise = new Promise(resolve => { reached = resolve; });
  const gate = new Promise(resolve => { release = resolve; });
  globalThis.RTCPeerConnection = class {
    static async generateCertificate() { return {}; }
    constructor() { this.connectionState = "new"; this.signalingState = "stable"; }
    addEventListener() {}
    setRemoteDescription() { reached(); return gate; }
    addTrack() { const sender = { getParameters: () => ({}) }; added.push(sender); return sender; }
    close() { this.connectionState = "closed"; }
  };
  t.after(() => { globalThis.RTCPeerConnection = previousPeer; });
  room.host = true;
  room.mailbox = { wait: async () => ({ sdp: CAMERA_SDP, pin: sdpFingerprint(CAMERA_SDP) }) };
  const pending = room.connect({}).catch(error => error);
  await reachedPromise;
  room.pause(TRIP, { resumable: true });
  release();
  const error = await pending;
  assert.equal(error.name, "AbortError");
  assert.equal(added.length, 0);
  assert.equal(room.sender, null);
  assert.equal(room.ended, undefined);
});

test("a restarted call signals on video-call-2 and connects", async t => {
  t.mock.timers.enable({ apis: ["Date", "setTimeout", "setInterval"], now: 1000000 });
  const previousDocument = globalThis.document;
  const previousPeer = globalThis.RTCPeerConnection;
  globalThis.document = { removeEventListener() {} };
  const slots = new Map();
  const invite = { capability: new Uint8Array(32).fill(7), session: "1".repeat(64), expiresAt: 1000000 / 1000 + 3600 };
  class FakePeer extends EventTarget {
    constructor() {
      super();
      this.connectionState = "connected";
      this.signalingState = "stable";
      this.iceGatheringState = "complete";
      this.localDescription = { sdp: CAMERA_SDP };
    }
    static async generateCertificate() { return {}; }
    setLocalDescription() { this.localDescription = { sdp: CAMERA_SDP }; }
    setRemoteDescription() {}
    addTrack() {
      const sender = { getParameters: () => ({ encodings: [{}] }), async setParameters() {} };
      this.transceivers = [{ sender, setCodecPreferences() {}, direction: "sendrecv" }];
      return sender;
    }
    getTransceivers() { return this.transceivers ?? []; }
    createOffer() { return { type: "offer", sdp: CAMERA_SDP }; }
    createAnswer() { return { type: "answer", sdp: CAMERA_SDP }; }
    close() { this.connectionState = "closed"; }
    getStats() { return new Map(); }
  }
  globalThis.RTCPeerConnection = FakePeer;
  const memory = () => async (path, options) => {
    if (!options?.body) return slots.has(path) ? { status: 200, body: { payload: slots.get(path) } } : { status: 204 };
    if (slots.has(path)) return { status: 409 };
    slots.set(path, JSON.parse(options.body).payload);
    return { status: 201 };
  };
  const make = host => {
    const room = Object.assign(Object.create(VideoRoom.prototype), {
      root: { dataset: {} }, origin: "https://relay.example", invite, host, timingReady: () => true,
      label: host ? "Show my camera" : "Show camera", call: 2, relayBudget: { counted: 0, watermark: 0 },
      camera: element(), switcher: element(), preview: element(), remote: element(), placeholder: element(), status: element(),
      credentials: { expiresAt: 1000000 + 600000, iceServers: [] },
      track: { readyState: "live", enabled: true, stop() {} },
    });
    room.startCall();
    room.mailbox.request = memory();
    return room;
  };
  const guest = make(false);
  const host = make(true);
  t.after(() => {
    guest.close();
    host.close();
    globalThis.document = previousDocument;
    globalThis.RTCPeerConnection = previousPeer;
  });
  assert.equal(guest.round(), "video-call-2");
  const guestDone = guest.connect({});
  for (let i = 0; i < 30 && slots.size === 0; i++) await setImmediate();
  assert.equal(slots.size, 1);
  await host.connect({});
  t.mock.timers.tick(2500);
  await guestDone;
  const offerId = await mailboxId(invite, "offer", 0, "video-call-2");
  const answerId = await mailboxId(invite, "answer", 0, "video-call-2");
  const firstCall = await mailboxId(invite, "offer", 0, "video");
  assert.ok([...slots.keys()].some(path => path.includes(offerId)));
  assert.ok([...slots.keys()].some(path => path.includes(answerId)));
  assert.equal([...slots.keys()].some(path => path.includes(firstCall)), false);
  assert.equal(guest.initialComplete, true);
  assert.equal(host.initialComplete, true);
  assert.equal(guest.state, "live");
  assert.equal(host.state, "live");
});

test("a restarted call renews on its own refresh rounds", async () => {
  const reads = [];
  const mailbox = { read: async round => { reads.push(round); return round === "video-refresh-abort" ? { v: 1, abort: "refresh" } : null; } };
  const refresh = new VideoRefresh({ peer: {}, mailbox, credentials: {}, host: true,
    signal: new AbortController().signal, onExpired() {}, prefix: "call-2-" });
  assert.equal(await refresh.observeAbort(), false, "an earlier call's abort does not stop this call");
  assert.equal(await refresh.latest(), 0);
  assert.deepEqual(reads, ["call-2-video-refresh-abort", "call-2-video-refresh-gen-1"]);
});
