import test from "node:test";
import assert from "node:assert/strict";
import { VideoRoom } from "../video-room.js";

function leavingRoom(t, { stream, preview, ...overrides }) {
  t.mock.method(globalThis, "navigator", () => ({ mediaDevices: {} }), { getter: true });
  const document = new EventTarget();
  Object.defineProperty(globalThis, "document", { value: document, configurable: true });
  t.after(() => { delete globalThis.document; });
  const controller = new AbortController();
  return Object.assign(Object.create(VideoRoom.prototype), {
    controller, signal: controller.signal, credentials: {},
    camera: new EventTarget(), switcher: new EventTarget(), status: {}, remote: {},
    capture: async () => stream, preview,
    render: () => assert.fail("Leave must prevent a later camera update"),
    ...overrides,
  });
}

test("Leave releases the replacement camera before a pending switch finishes", async (t) => {
  let replaceStarted;
  const replacing = new Promise(resolve => { replaceStarted = resolve; });
  let replaceComplete;
  const replacement = new Promise(resolve => { replaceComplete = resolve; });
  const previousTrack = { readyState: "live", stop() {} };
  const nextTrack = Object.assign(new EventTarget(), {
    readyState: "live", getSettings: () => ({}), stop() { this.readyState = "ended"; },
  });
  const stream = { getTracks: () => [nextTrack], getVideoTracks: () => [nextTrack] };
  const room = leavingRoom(t, {
    stream, peer: { close() {} }, track: previousTrack, initialComplete: true,
    preview: { srcObject: null, play: async () => {} },
    sender: { replaceTrack(track) { replaceStarted(track); return replacement; } },
  });

  const switching = room.tap(true);
  assert.equal(await replacing, nextTrack);
  room.close();
  assert.equal(nextTrack.readyState, "ended");
  assert.equal(room.preview.srcObject, null);
  replaceComplete();
  await switching;
  assert.equal(room.track, null);
  assert.equal(nextTrack.readyState, "ended");
  assert.equal(room.preview.srcObject, null);
});

test("Leave releases a new camera even while preview playback is pending", async (t) => {
  const { promise: previewStarted, resolve: started } = Promise.withResolvers();
  const { promise: playback, resolve: finish } = Promise.withResolvers();
  const track = { readyState: "live", stop() { this.readyState = "ended"; } };
  const stream = { getVideoTracks: () => [track], getTracks: () => [track] };
  const room = leavingRoom(t, {
    stream, timingReady: () => true,
    preview: { srcObject: null, play() { started(); return playback; } },
  });
  const tapping = room.tap();
  await previewStarted;
  room.close();
  assert.equal(track.readyState, "ended");
  assert.equal(room.preview.srcObject, null);
  finish();
  await tapping;
  assert.equal(room.peer, undefined);
  assert.equal(room.pendingCapture, null);
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
    camera: {}, switcher: {}, status: {}, initialComplete: true, peer: { connectionState: "connecting" },
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

test("a camera path failure releases the camera and can reuse the unwritten slot", async (t) => {
  const peers = [];
  const writes = [];
  const fingerprint = Array(32).fill("A1").join(":");
  const sdp = [
    "v=0", "m=video 9 UDP/TLS/RTP/SAVPF 96", "a=sendrecv", "a=rtpmap:96 VP8/90000",
    "a=fingerprint:sha-256 " + fingerprint,
  ].join("\r\n");
  const allowed = sdp + "\r\na=candidate:1 1 udp 100 8.8.8.8 10500 typ srflx";
  class Peer extends EventTarget {
    static async generateCertificate() { return {}; }
    constructor() {
      super();
      this.iceGatheringState = "complete";
      this.connectionState = "connecting";
      this.transceiver = { sender: { getParameters: () => ({}) } };
      peers.push(this);
    }
    addTrack(track, stream) {
      assert.equal(stream.getVideoTracks()[0], track);
      return this.transceiver.sender;
    }
    getTransceivers() { return [this.transceiver]; }
    async createOffer() { return { sdp: peers.length === 1 ? sdp : allowed }; }
    async setLocalDescription(description) { this.localDescription = description; }
    async setRemoteDescription() {}
    close() {
      this.connectionState = "closed";
      this.dispatchEvent(new Event("connectionstatechange"));
    }
  }
  Object.defineProperty(globalThis, "RTCPeerConnection", { value: Peer, configurable: true });
  t.after(() => { delete globalThis.RTCPeerConnection; });
  const capture = () => {
    const track = Object.assign(new EventTarget(), {
      enabled: true, readyState: "live", getSettings: () => ({}),
      stop() { this.readyState = "ended"; },
    });
    return { getVideoTracks: () => [track], getTracks: () => [track] };
  };
  const failed = Promise.withResolvers();
  const room = Object.assign(Object.create(VideoRoom.prototype), {
    host: false, signal: new AbortController().signal, invite: { expiresAt: Infinity },
    credentials: { expiresAt: Infinity }, timingReady: () => true, capture,
    preview: { play: async () => {} }, camera: {}, switcher: {}, remote: {}, status: {},
    mailbox: {
      async write(round, kind) { writes.push([round, kind]); },
      async wait() { return { sdp: allowed, pin: "a1".repeat(32) }; },
    },
    render(state, message) {
      this.state = state;
      this.status.textContent = message;
      if (state === "camera-error") failed.resolve(message);
    },
  });

  let connection;
  room.connect = () => {
    connection = VideoRoom.prototype.connect.call(room);
    return connection;
  };
  await room.tap();
  const first = room.track;
  assert.match(await failed.promise, /Tap Try again.*Music stays connected/);
  assert.equal(first.readyState, "ended");
  assert.equal(peers[0].connectionState, "closed");
  assert.equal(room.peer, null);
  assert.equal(room.preview.srcObject, null);
  assert.deepEqual(writes, []);

  await room.tap();
  await connection;
  assert.equal(room.initialComplete, true);
  assert.equal(peers.length, 2);
  assert.equal(room.track.readyState, "live");
  assert.deepEqual(writes, [["video", "offer"]]);
});
