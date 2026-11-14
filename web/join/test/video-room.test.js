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
