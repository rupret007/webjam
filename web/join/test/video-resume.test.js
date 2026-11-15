import test from "node:test";
import assert from "node:assert/strict";
import { setImmediate } from "node:timers/promises";
import { MAX_CAMERA_CALLS, VideoRoom } from "../video-room.js";
import { VideoRefresh } from "../video-refresh.js";

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

test("a restarted call renews on its own refresh rounds", async () => {
  const reads = [];
  const mailbox = { read: async round => { reads.push(round); return round === "video-refresh-abort" ? { v: 1, abort: "refresh" } : null; } };
  const refresh = new VideoRefresh({ peer: {}, mailbox, credentials: {}, host: true,
    signal: new AbortController().signal, onExpired() {}, prefix: "call-2-" });
  assert.equal(await refresh.observeAbort(), false, "an earlier call's abort does not stop this call");
  assert.equal(await refresh.latest(), 0);
  assert.deepEqual(reads, ["call-2-video-refresh-abort", "call-2-video-refresh-gen-1"]);
});
