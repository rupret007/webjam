import test from "node:test";
import assert from "node:assert/strict";
import { VideoRoom } from "../video-room.js";

test("a camera switch finishing after Leave releases the replacement track", async () => {
  let replaceStarted;
  const replacing = new Promise(resolve => { replaceStarted = resolve; });
  let replaceComplete;
  const replacement = new Promise(resolve => { replaceComplete = resolve; });
  const previousTrack = { readyState: "live", stop() {} };
  const nextTrack = Object.assign(new EventTarget(), {
    readyState: "live", getSettings: () => ({}), stop() { this.readyState = "ended"; },
  });
  const stream = { getTracks: () => [nextTrack], getVideoTracks: () => [nextTrack] };
  const controller = new AbortController();
  const room = Object.assign(Object.create(VideoRoom.prototype), {
    signal: controller.signal, credentials: {}, peer: {},
    track: previousTrack, camera: {}, switcher: {}, status: {}, capture: async () => stream,
    preview: { srcObject: null, play: async () => {} },
    sender: { replaceTrack(track) { replaceStarted(track); return replacement; } },
    render: () => assert.fail("Leave must prevent a later camera update"),
  });

  const switching = room.tap(true);
  assert.equal(await replacing, nextTrack);
  controller.abort();
  room.track = null;
  room.preview.srcObject = null;
  replaceComplete();
  await switching;
  assert.equal(room.track, null);
  assert.equal(nextTrack.readyState, "ended");
  assert.equal(room.preview.srcObject, null);
});

test("Switch camera starts capture during the tap using the current facing mode or cached devices", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "navigator", () => ({ mediaDevices: {
    enumerateDevices() { assert.fail("Switch cannot enumerate before requesting capture"); },
    async getUserMedia(constraints) { calls.push(constraints); },
  } }), { getter: true });

  const room = Object.assign(Object.create(VideoRoom.prototype), {
    facing: "user",
    track: { getSettings: () => ({ deviceId: "first" }) },
  });
  const mobile = room.capture(true);
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].video.facingMode, { exact: "environment" });
  await mobile;

  room.cameraDevices = [{ deviceId: "first" }, { deviceId: "second" }];
  const desktop = room.capture(true);
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[1].video.deviceId, { exact: "second" });
  assert.ok(calls.every(request => request.audio === false));
  await desktop;
});

test("a failed switch restores the local preview without falsely showing the remote camera", async () => {
  const track = { readyState: "live", enabled: true };
  const oldPreview = { getVideoTracks: () => [track] };
  const preview = { srcObject: oldPreview, play: async () => {} };
  const rejected = { readyState: "live", stop() { this.readyState = "ended"; } };
  const stream = { getVideoTracks: () => [rejected], getTracks: () => [rejected] };
  const room = Object.assign(Object.create(VideoRoom.prototype), {
    signal: new AbortController().signal, credentials: {}, track, preview,
    camera: {}, switcher: {}, status: {}, peer: { connectionState: "connecting" },
    sender: { async replaceTrack() { throw new Error("Switch refused"); } },
    capture: async () => stream,
    render(state, message) {
      this.state = state;
      this.status.textContent = message;
    },
  });

  await room.tap(true);
  assert.equal(preview.srcObject, oldPreview);
  assert.equal(room.track, track);
  assert.equal(rejected.readyState, "ended");
  assert.equal(room.state, "preview");
  assert.match(room.status.textContent, /Your current camera is still on/);
});
