import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createInvite, desktopSlotAad, fromBase64Url, fromHex, inviteFragment, mailboxId, mailboxKey, open, readInvite, roomAddress, seal, toBase64Url, toHex, turnToken } from "../crypto.js";
import { MUSIC_CONSTRAINTS, acquireMusicStream, constraintWarnings, latencyMs, musicSdp, tuneReceiver } from "../audio.js";
import { BUSY_MESSAGE, MailboxTransport, SketchRoom, delay } from "../room.js";

const vectors = JSON.parse(await readFile(new URL("../../../tests/fixtures/mailbox-crypto-vectors.json", import.meta.url)));
for (const vector of vectors) {
  test(`desktop shared HKDF and AEAD vector: ${vector.name}`, async () => {
    const capability = fromHex(vector.capability_hex);
    const session = fromHex(vector.session_reference_hex);
    assert.equal(await mailboxId(capability), vector.mailbox_id);
    const key = await mailboxKey(capability, session);
    assert.equal(toHex(key), vector.mailbox_key_hex);
    for (const slot of ["offer", "answer"]) {
      const aad = desktopSlotAad(slot, session, fromHex(vector.host_spki_sha256_hex));
      assert.equal(await open(key, vector[`${slot}_payload`], aad), vector.plaintext_utf8);
      const encrypted = await seal(key, vector.plaintext_utf8, aad);
      assert.equal(await open(key, encrypted, aad), vector.plaintext_utf8);
      assert.notEqual(encrypted, vector[`${slot}_payload`]);
    }
  });
}

test("AEAD rejects changed ciphertext, keys, address, and slot", async () => {
  const invite = createInvite();
  const material = await roomAddress(invite.capability, invite.sessionId, "pair/0-1/direct/offer/0", "offer");
  const payload = await seal(material.key, '{"sdp":"a=fingerprint:sha-256 secret"}', material.aad);
  assert.equal(await open(material.key, payload, material.aad), '{"sdp":"a=fingerprint:sha-256 secret"}');
  const changed = fromBase64Url(payload);
  changed[15] ^= 1;
  await assert.rejects(open(material.key, toBase64Url(changed), material.aad));
  await assert.rejects(open(new Uint8Array(32), payload, material.aad));
  for (const [address, slot] of [["pair/0-2/direct/offer/0", "offer"], ["pair/0-1/direct/offer/0", "answer"]]) {
    const other = await roomAddress(invite.capability, invite.sessionId, address, slot);
    await assert.rejects(open(other.key, payload, other.aad));
  }
  await assert.rejects(seal(material.key, "x".repeat(4096), material.aad), /limit/);
});

test("fromBase64Url rejects noncanonical encodings", () => {
  const canonical = toBase64Url(crypto.getRandomValues(new Uint8Array(32)));
  assert.throws(() => fromBase64Url(canonical.slice(0, -1)), /Noncanonical encoded data/);
});

test("readInvite rejects invite fragments with extra keys", () => {
  const invite = createInvite(1000);
  const fragment = inviteFragment(invite);
  assert.throws(() => readInvite(`#${fragment}&extra=1`, 1000), /Invalid invite link/);
});

test("invite stays entirely in the fragment and expires at ten minutes", async () => {
  const now = 1234567890;
  const invite = createInvite(now);
  const url = new URL("https://stalemate.band/sketch-jam/");
  url.hash = inviteFragment(invite);
  assert.equal(url.search, "");
  assert.equal(url.pathname, "/sketch-jam/");
  assert.deepEqual(readInvite(url.hash, now + 599999), invite);
  assert.throws(() => readInvite(url.hash, now + 600000), /expired/);
  assert.throws(() => readInvite(`${url.hash}&k=AAAA`, now), /Invalid/);
  assert.throws(() => readInvite(`${url.hash}&relay=https://evil.example`, now), /Invalid/);
  assert.throws(() => readInvite(url.hash.replace(/t=\d+/, "t="), 1000), /Invalid invite link/);
  const params = new URLSearchParams(url.hash.replace(/^#/, ""));
  assert.throws(() => readInvite(`#k=${params.get("k")}&s=${params.get("s")}&s=${params.get("s")}`, 1000), /Invalid invite link/);
  assert.throws(() => readInvite(url.hash, now - 31000), /expired/);
  const token = await turnToken(invite.capability, invite.sessionId);
  assert.match(token, /^[A-Za-z0-9_-]{43}$/);
  assert.notEqual(token, toBase64Url(invite.capability));
  assert.notEqual(token, await turnToken(invite.capability, crypto.randomUUID()));
});

const audioSdp = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111 0\r\na=rtpmap:111 opus/48000/2\r\na=fmtp:111 minptime=10;useinbandfec=0;stereo=0\r\na=ptime:60\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=rtpmap:96 VP8/90000\r\na=fmtp:96 x-google-start-bitrate=1000\r\n";
test("music SDP sets Opus stereo, bitrate, FEC, DTX and audio packet time without changing video", () => {
  const output = musicSdp(audioSdp);
  assert.match(output, /a=fmtp:111 minptime=10;useinbandfec=1;stereo=1;sprop-stereo=1;maxaveragebitrate=128000;usedtx=0\r\n/);
  assert.match(output, /a=ptime:20\r\nm=video/);
  assert.equal(output.split("m=video")[1], audioSdp.split("m=video")[1]);
  assert.equal(musicSdp(output), output);
  assert.match(musicSdp(audioSdp, 10), /a=ptime:10\r\n/);
  assert.throws(() => musicSdp(audioSdp, 5), /packet time/);
});

test("music SDP adds missing fmtp and leaves non-Opus audio untouched", () => {
  assert.match(musicSdp("v=0\nm=audio 9 UDP/TLS/RTP/SAVPF 109\na=rtpmap:109 opus/48000/2\n"), /a=fmtp:109 stereo=1;sprop-stereo=1;maxaveragebitrate=128000;usedtx=0;useinbandfec=1\r\na=ptime:20/);
  const pcm = "v=0\r\nm=audio 9 RTP/AVP 0\r\na=rtpmap:0 PCMU/8000\r\n";
  assert.equal(musicSdp(pcm), pcm);
});

test("constraint warnings identify every ignored or unreported music setting", () => {
  assert.deepEqual(constraintWarnings(MUSIC_CONSTRAINTS), []);
  const warnings = constraintWarnings({ echoCancellation: true, channelCount: 1, sampleRate: 44100, noiseSuppression: false });
  assert.equal(warnings.length, 4);
  assert.ok(warnings.some((warning) => warning.includes("autoGainControl") && warning.includes("not reported")));
  assert.ok(warnings.some((warning) => warning.includes("channelCount") && warning.includes("actual 1")));
});

test("capture fallback preserves disabled processing, reports fallback and never retries permission denial", async () => {
  const requests = [];
  const stream = { getAudioTracks: () => [{ getSettings: () => ({ ...MUSIC_CONSTRAINTS, channelCount: 1 }) }] };
  const acquired = await acquireMusicStream({ getUserMedia: async (constraints) => {
    requests.push(constraints);
    if (requests.length === 1) throw new DOMException("unsupported", "OverconstrainedError");
    return stream;
  } });
  assert.deepEqual(requests[0], { video: false, audio: MUSIC_CONSTRAINTS });
  assert.deepEqual(requests[1].audio, { echoCancellation: false, noiseSuppression: false, autoGainControl: false });
  assert.equal(acquired.stream, stream);
  assert.equal(acquired.warnings.length, 2);
  let calls = 0;
  await assert.rejects(acquireMusicStream({ getUserMedia: async () => { calls++; throw new DOMException("denied", "NotAllowedError"); } }), { name: "NotAllowedError" });
  assert.equal(calls, 1);
});

test("capture closes unexpected multiple audio tracks", async () => {
  let stops = 0;
  const tracks = Array.from({ length: 2 }, () => ({ stop: () => stops++ }));
  await assert.rejects(acquireMusicStream({ getUserMedia: async () => ({ getAudioTracks: () => tracks, getTracks: () => tracks }) }), /single audio/);
  assert.equal(stops, 2);
});

test("latency estimate uses selected pair and audio jitter buffer plus local latency", () => {
  const stats = new Map([
    ["pair", { type: "candidate-pair", nominated: true, state: "succeeded", currentRoundTripTime: 0.06 }],
    ["in", { type: "inbound-rtp", kind: "audio", jitterBufferDelay: 3, jitterBufferEmittedCount: 100 }],
  ]);
  assert.equal(latencyMs(stats, 0.01), 69.99999999999999);
  assert.equal(latencyMs(new Map(), 0.01), null);
  const receiver = { jitterBufferTarget: 100 };
  tuneReceiver(receiver);
  assert.equal(receiver.jitterBufferTarget, 20);
  assert.doesNotThrow(() => tuneReceiver({}));
});

function loopback(invite = createInvite()) {
  const slots = new Map();
  const requests = [];
  const transport = new MailboxTransport("http://127.0.0.1:4173", invite, {
    interval: 0,
    fetchImpl: async (url, options) => {
      const path = new URL(url).pathname;
      assert.equal(new URL(url).search, "?client=web");
      assert.equal(options.credentials, "omit");
      assert.equal(options.cache, "no-store");
      requests.push({ path, options });
      if (options.method === "POST") {
        if (slots.has(path)) return Response.json({ error: "duplicate" }, { status: 409 });
        const { payload } = JSON.parse(options.body);
        assert.match(path, /^\/mailbox\/[0-9a-f]{64}\/(offer|answer)$/);
        assert.match(payload, /^[A-Za-z0-9_-]+$/);
        assert.ok(payload.length <= 4096);
        slots.set(path, payload);
        return Response.json({ error: "ok" }, { status: 201 });
      }
      return slots.has(path) ? Response.json({ payload: slots.get(path) }) : new Response(null, { status: 204 });
    },
  });
  return { transport, requests, slots };
}

test("large encrypted SDP traverses bounded write-once mailbox slots and round trips intact", async () => {
  const { transport, requests } = loopback();
  const description = { type: "offer", sdp: `${audioSdp}a=fingerprint:sha-256 ${"AB:".repeat(31)}AB\r\n${"a=candidate:1234 1 udp 12345 192.168.1.2 12345 typ host\r\n".repeat(100)}` };
  await transport.sendDescription("pair/0-1/direct", "offer", description);
  assert.ok(requests.length > 1);
  assert.deepEqual(await transport.receiveDescription("pair/0-1/direct", "offer"), description);
  for (const request of requests) {
    assert.ok(!request.options.body?.includes("fingerprint"));
    assert.ok(!request.options.body?.includes("192.168"));
  }
  await assert.rejects(transport.sendDescription("pair/0-1/direct", "offer", description), /already been used/);
});

test("room membership writes are atomic and oversized encrypted manifests are rejected", async () => {
  const { transport } = loopback();
  assert.equal(await transport.get("member/0", "offer"), null);
  assert.equal(await transport.put("member/0", "offer", { id: "a", name: "Alice" }), true);
  assert.equal(await transport.put("member/0", "offer", { id: "b", name: "Bob" }), false);
  assert.deepEqual(await transport.get("member/0", "offer"), { id: "a", name: "Alice" });
  await transport.put("bad/offer/0", "offer", { part: 0, total: 41, text: "x" });
  await assert.rejects(transport.receiveDescription("bad", "offer"), /Invalid encrypted signaling/);
});

test("web TURN request uses a separated capability and enforces short-lived bounded credentials", async () => {
  const invite = createInvite();
  let body;
  let ttl = 3600;
  const transport = new MailboxTransport("https://relay.example", invite, { interval: 0, fetchImpl: async (url, options) => {
    assert.equal(new URL(url).pathname, "/turn-credentials");
    body = JSON.parse(options.body);
    return Response.json({ iceServers: [{ urls: "turn:relay.example", username: "ephemeral", credential: "ephemeral" }], ttl, quota_bytes: 256 * 1024 * 1024 });
  } });
  const result = await transport.turnCredentials();
  assert.equal(result.iceServers.length, 1);
  assert.equal(body.client, "web");
  assert.equal(body.session_id, invite.sessionId);
  assert.equal(body.invite_token, await turnToken(invite.capability, invite.sessionId));
  assert.notEqual(body.invite_token, toBase64Url(invite.capability));
  ttl = 3601;
  await assert.rejects(transport.turnCredentials(), new RegExp(BUSY_MESSAGE.replaceAll(".", "\\.")));
});

for (const refreshing of [false, true]) {
  test(`later peers retry a rejected TURN ${refreshing ? "refresh" : "request"} and reuse successful credentials`, async (t) => {
    const original = globalThis.RTCPeerConnection;
    t.after(() => {
      if (original === undefined) delete globalThis.RTCPeerConnection;
      else globalThis.RTCPeerConnection = original;
    });
    globalThis.RTCPeerConnection = class extends EventTarget {
      constructor({ iceServers }) {
        super();
        this.iceServers = iceServers;
        this.iceGatheringState = "complete";
        this.connectionState = "new";
      }
      addTrack() {}
      async createOffer() { return { type: "offer", sdp: audioSdp }; }
      async setLocalDescription(description) { this.localDescription = description; }
      async setRemoteDescription() {}
      getReceivers() {
        queueMicrotask(() => {
          this.connectionState = this.iceServers[0].urls.startsWith("turn:") ? "connected" : "failed";
          this.dispatchEvent(new Event("connectionstatechange"));
        });
        return [];
      }
      close() { this.connectionState = "closed"; }
    };
    const track = { stop() {} };
    const room = new SketchRoom({
      origin: "https://relay.example", invite: createInvite(),
      stream: { getAudioTracks: () => [track], getTracks: () => [track] },
      audioContext: { baseLatency: 0 }, onStatus() {}, onPeer() {}, onTrack() {}, onError() {},
    });
    t.after(() => room.close());
    room.index = 3;
    const credentials = { iceServers: [{ urls: "turn:relay.example" }], expiresAt: Date.now() + 60_000, quotaBytes: 1024 };
    if (refreshing) room.turn = Promise.resolve({ ...credentials, expiresAt: Date.now() });
    const unavailable = new Error("TURN temporarily unavailable");
    let requests = 0;
    room.transport.turnCredentials = async () => {
      if (++requests === 1) throw unavailable;
      return credentials;
    };
    room.transport.sendDescription = async () => {};
    room.transport.receiveDescription = async () => ({ type: "answer", sdp: audioSdp });
    room.transport.put = async () => true;

    await assert.rejects(room.connectPeer(0, { name: "First peer" }), (error) => error === unavailable);
    await room.connectPeer(1, { name: "Second peer" });
    assert.equal(requests, 2);
    assert.equal(room.connections.get(1).connectionState, "connected");
    assert.deepEqual(room.connections.get(1).iceServers, credentials.iceServers);

    await room.connectPeer(2, { name: "Third peer" });
    assert.equal(requests, 2);
    assert.equal(room.connections.get(2).connectionState, "connected");
  });
}

test("closing a room cancels pending pacing without sending another request", async () => {
  const controller = new AbortController();
  let calls = 0;
  const transport = new MailboxTransport("https://relay.example", createInvite(), { signal: controller.signal, interval: 10000, fetchImpl: async () => { calls++; return new Response(null, { status: 204 }); } });
  await transport.get("member/0", "offer");
  const pending = transport.get("member/1", "offer");
  controller.abort();
  await assert.rejects(pending, { name: "AbortError" });
  assert.equal(calls, 1);
  await assert.rejects(delay(1000, controller.signal), { name: "AbortError" });
});
