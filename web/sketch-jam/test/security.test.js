import assert from "node:assert/strict";
import test from "node:test";
import { createInvite, open, roomAddress, seal, toBase64Url, toHex, turnToken } from "../crypto.js";
import { BUSY_MESSAGE, MailboxTransport, SketchRoom } from "../room.js";

function transport(fetchImpl, options = {}) {
  const invite = createInvite();
  return new MailboxTransport("https://relay.example", invite, {
    interval: 0, fetchImpl, signal: new AbortController().signal, ...options,
  });
}

test("mailbox HTTP carries derived addresses and ciphertext without the fragment capability or plaintext", async () => {
  const seen = [];
  const mailbox = transport(async (url, init) => {
    seen.push({ url, init });
    return Response.json({ error: "ok" }, { status: 201 });
  });
  const value = { type: "offer", sdp: "v=0\r\na=fingerprint:sha-256 PRIVATE-FINGERPRINT\r\n", name: "Private musician" };
  assert.equal(await mailbox.put("pair/0-1/direct/offer/0", "offer", value), true);
  const { url, init } = seen[0];
  assert.match(url, /^https:\/\/relay\.example\/mailbox\/[0-9a-f]{64}\/offer\?client=web$/);
  assert.equal(init.credentials, "omit");
  assert.equal(init.referrerPolicy, "no-referrer");
  assert.equal(init.cache, "no-store");
  assert.deepEqual(Object.keys(JSON.parse(init.body)), ["payload"]);
  const sent = `${url}\n${init.body}`;
  for (const privateValue of [toBase64Url(mailbox.invite.capability), toHex(mailbox.invite.capability), mailbox.invite.sessionId, value.name, "PRIVATE-FINGERPRINT"]) {
    assert.equal(sent.includes(privateValue), false);
  }
  const material = await mailbox.address("pair/0-1/direct/offer/0", "offer");
  assert.deepEqual(JSON.parse(await open(material.key, JSON.parse(init.body).payload, material.aad)), value);
});

test("TURN HTTP sends a domain-separated token without disclosing the invite capability", async () => {
  let sent;
  const mailbox = transport(async (_url, init) => {
    sent = JSON.parse(init.body);
    return Response.json({ iceServers: [{ urls: "turn:relay.example:3478", username: "short", credential: "lived" }], ttl: 600, quota_bytes: 256 * 1024 * 1024 });
  });
  await mailbox.turnCredentials();
  assert.equal(sent.client, "web");
  assert.equal(sent.session_id, mailbox.invite.sessionId);
  assert.match(sent.invite_token, /^[A-Za-z0-9_-]{32,256}$/);
  assert.equal(sent.invite_token, await turnToken(mailbox.invite.capability, mailbox.invite.sessionId));
  assert.notEqual(sent.invite_token, toBase64Url(mailbox.invite.capability));
});

test("mailbox ciphertext cannot be replayed into another peer, round, slot, or session", async () => {
  const invite = createInvite();
  const original = await roomAddress(invite.capability, invite.sessionId, "pair/0-1/direct/offer/0", "offer");
  const payload = await seal(original.key, JSON.stringify({ fingerprint: "private" }), original.aad);
  const targets = [
    [invite.sessionId, "pair/0-2/direct/offer/0", "offer"],
    [invite.sessionId, "pair/0-1/relay/offer/0", "offer"],
    [invite.sessionId, "pair/0-1/direct/offer/0", "answer"],
    [crypto.randomUUID(), "pair/0-1/direct/offer/0", "offer"],
  ];
  for (const [session, address, slot] of targets) {
    const other = await roomAddress(invite.capability, session, address, slot);
    await assert.rejects(() => open(other.key, payload, other.aad));
  }
});

test("a forged chunk count is rejected before it can amplify mailbox polling", async () => {
  let calls = 0;
  let payload;
  const mailbox = transport(async () => {
    calls += 1;
    return Response.json({ payload });
  });
  const material = await mailbox.address("pair/0-1/direct/offer/0", "offer");
  payload = await seal(material.key, JSON.stringify({ part: 0, total: 41, text: "{}" }), material.aad);
  await assert.rejects(() => mailbox.receiveDescription("pair/0-1/direct", "offer"), /Invalid encrypted signaling/);
  assert.equal(calls, 1);
});

test("free-tier HTML or empty cap and outage responses show relay busy without retrying", async () => {
  for (const status of [429, 503]) {
    for (const body of ["", "<html>Free plan limit reached</html>", JSON.stringify({ error: "cap" })]) {
      let calls = 0;
      const mailbox = transport(async () => {
        calls += 1;
        return new Response(body, { status });
      });
      await assert.rejects(() => mailbox.request("/turn-credentials"), (error) => error.message === BUSY_MESSAGE && error.status === status);
      assert.equal(calls, 1);
    }
  }
});

test("leaving during a rate-limit backoff cancels the pending retry", async () => {
  const controller = new AbortController();
  let calls = 0;
  const mailbox = transport(async () => {
    calls += 1;
    return Response.json({ error: "rate" }, { status: 429 });
  }, { signal: controller.signal, onStatus: () => controller.abort() });
  await assert.rejects(() => mailbox.request("/turn-credentials"), { name: "AbortError" });
  assert.equal(calls, 1);
});

test("closing a room cancels in-flight signaling and stops every local track and peer", async () => {
  let stops = 0;
  let closes = 0;
  let started;
  const requestStarted = new Promise((resolve) => { started = resolve; });
  const room = new SketchRoom({
    origin: "https://relay.example", invite: createInvite(),
    stream: { getTracks: () => [{ stop: () => { stops += 1; } }] },
    audioContext: { baseLatency: 0 }, onStatus() {}, onPeer() {}, onTrack() {}, onError() {},
  });
  room.connections.set(1, { close: () => { closes += 1; } });
  room.transport.interval = 0;
  room.transport.fetchImpl = async (_url, { signal }) => new Promise((_resolve, reject) => {
    signal.addEventListener("abort", () => reject(new DOMException("Stopped", "AbortError")), { once: true });
    started();
  });
  const pending = room.transport.request("/mailbox/test/offer?client=web");
  await requestStarted;
  room.close();
  await assert.rejects(() => pending, { name: "AbortError" });
  assert.equal(stops, 1);
  assert.equal(closes, 1);
  assert.equal(room.connections.size, 0);
  assert.equal(room.controller.signal.aborted, true);
});

function statsRoom(reports, { relay = true, getStatsError = null } = {}) {
  const errors = [];
  let closed = 0;
  const room = new SketchRoom({
    origin: "https://relay.example", invite: createInvite(),
    stream: { getTracks: () => [{ stop() {} }] }, audioContext: { baseLatency: 0 },
    onStatus() {}, onPeer() {}, onTrack() {}, onError: (error) => errors.push(error),
  });
  room.participants.set(1, { name: "Peer" });
  room.connections.set(1, {
    connectionState: "connected",
    getStats: async () => {
      if (getStatsError) throw getStatsError;
      return new Map(reports.map((report, index) => [String(index), report]));
    },
    close: () => { closed += 1; },
  });
  if (relay) room.relayPeers.add(1);
  return { room, errors, get closed() { return closed; } };
}

test("relay traffic accounting fails closed on missing, nonfinite, or negative byte counters", async () => {
  for (const reports of [
    [],
    [{ type: "candidate-pair", bytesSent: 0, bytesReceived: 0 }],
    [{ type: "transport", bytesReceived: 10 }],
    [{ type: "transport", bytesSent: 10 }],
    [{ type: "transport", bytesSent: Number.NaN, bytesReceived: 10 }],
    [{ type: "transport", bytesSent: 10, bytesReceived: Number.POSITIVE_INFINITY }],
    [{ type: "transport", bytesSent: -1, bytesReceived: 10 }],
    [{ type: "transport", bytesSent: 10, bytesReceived: -1 }],
    [{ type: "transport", bytesSent: "10", bytesReceived: 10 }],
  ]) {
    const fixture = statsRoom(reports);
    await fixture.room.updateStats();
    assert.equal(fixture.errors.length, 1, JSON.stringify(reports));
    assert.equal(fixture.errors[0].message, BUSY_MESSAGE);
    assert.equal(fixture.errors[0].status, 503);
    assert.equal(fixture.room.controller.signal.aborted, true);
    assert.equal(fixture.closed, 1);
  }
});

test("relay stats failure closes the session while direct latency stats can be unavailable", async () => {
  const error = new Error("Stats unavailable");
  const relay = statsRoom([], { getStatsError: error });
  await relay.room.updateStats();
  assert.equal(relay.errors.length, 1);
  assert.equal(relay.errors[0].status, 503);
  assert.equal(relay.closed, 1);
  const direct = statsRoom([], { relay: false, getStatsError: error });
  await direct.room.updateStats();
  assert.equal(direct.errors.length, 0);
  assert.equal(direct.room.controller.signal.aborted, false);
  direct.room.close();
});

test("relay byte accounting accepts zero and sums every peer before the session safety limit", async () => {
  const zero = statsRoom([{ type: "transport", bytesSent: 0, bytesReceived: 0 }]);
  await zero.room.updateStats();
  assert.equal(zero.errors.length, 0);
  zero.room.close();

  const fixture = statsRoom([{ type: "transport", bytesSent: 400, bytesReceived: 20 }]);
  fixture.room.relayLimit = 1000;
  fixture.room.relayPeers.add(2);
  fixture.room.participants.set(2, { name: "Second peer" });
  fixture.room.connections.set(2, {
    connectionState: "connected",
    getStats: async () => new Map([["transport", { type: "transport", bytesSent: 450, bytesReceived: 30 }]]),
    close() {},
  });
  await fixture.room.updateStats();
  assert.equal(fixture.errors.length, 1);
  assert.equal(fixture.errors[0].status, 429);
  assert.equal(fixture.errors[0].message, BUSY_MESSAGE);
  assert.equal(fixture.room.controller.signal.aborted, true);
});
