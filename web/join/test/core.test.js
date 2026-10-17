import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { DOMAIN, fromHex, toHex, fromBase64Url, toBase64Url, hkdf, mailboxId, turnToken, seal, open, enrollmentProof, readInvite, validateOffer, sdpFingerprint } from "../crypto.js";
import { MUSIC_CONSTRAINTS, acquireMusicStream, constraintWarnings, musicSdp, latencyMs } from "../audio.js";
import { publicIPv4, candidateAllowed, policySdp, validateCredentials, SESSION_BYTES } from "../policy.js";
import { Mailbox, BUSY_MESSAGE } from "../mailbox.js";
import { WebJoinSession } from "../session.js";
const vector = JSON.parse(await readFile(new URL("vectors.json", import.meta.url)));
const invite = { capability: fromHex(vector.capability_hex), session: vector.session, generation: vector.generation, expiresAt: vector.expires_at };
const fingerprint = (hex) => hex.match(/../g).join(":").toUpperCase();
const candidate = (ip, kind = "srflx") => `a=candidate:1 1 udp 12345 ${ip} 42345 typ ${kind} raddr 0.0.0.0 rport 0`;
const sdp = `v=0\r\na=fingerprint:sha-256 ${fingerprint(vector.host_pin)}\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=rtpmap:111 opus/48000/2\r\n${candidate("8.8.8.8")}\r\n`;
const offer = { ...vector.answer, kind: "offer", guest_pin: "", sdp };
const credentials = () => ({ client: "web", ttl: 600, quota_bytes: SESSION_BYTES, allowed_relay_ips: ["141.101.90.1", "162.159.207.1"], iceServers: [{ urls: ["turn:turn.cloudflare.com:3478?transport=udp", "turns:turn.cloudflare.com:443?transport=tcp"], username: "temporary", credential: "temporary" }] });

test("shared Go/browser HKDF, ciphertext, mailbox, TURN and enrollment vectors", async () => {
  assert.equal(toHex(await hkdf(invite.capability, fromHex(invite.session), `${DOMAIN}/aead`)), vector.aead_key_hex);
  assert.equal(toHex(await hkdf(invite.capability, fromHex(invite.session), `${DOMAIN}/enrollment`)), vector.enrollment_key_hex);
  assert.equal(await mailboxId(invite, "offer", 0), vector.mailbox_offer_0);
  assert.equal(await turnToken(invite), vector.turn_token);
  assert.equal(toHex(await enrollmentProof(invite, vector.answer)), vector.proof_hex);
  assert.equal(toBase64Url(await seal(invite, vector.answer, fromHex(vector.aes_nonce_hex))), vector.envelope_b64url);
  assert.deepEqual(await open(invite, fromBase64Url(vector.envelope_b64url)), vector.answer);
});
test("AEAD rejects ciphertext tampering, wrong session, wrong capability and oversized envelopes", async () => {
  const bytes = fromBase64Url(vector.envelope_b64url);
  bytes[30] ^= 1;
  await assert.rejects(open(invite, bytes));
  await assert.rejects(open({ ...invite, session: "ab".repeat(32) }, fromBase64Url(vector.envelope_b64url)));
  await assert.rejects(open({ ...invite, capability: fromHex("ab".repeat(32)) }, fromBase64Url(vector.envelope_b64url)));
  await assert.rejects(seal(invite, { sdp: "x".repeat(8192) }), /limit/);
});
test("enrollment proof binds every session, role, generation, expiry, nonce and certificate field", async () => {
  for (const [key, value] of Object.entries({ session: "ef".repeat(32), generation: 8, expires_at: 2000000001, nonce: "ff".repeat(16), host_pin: "ee".repeat(32), guest_pin: "dd".repeat(32) })) {
    assert.notEqual(toHex(await enrollmentProof(invite, { ...vector.answer, [key]: value })), vector.proof_hex, key);
  }
});
test("fragment parser rejects expiry, future lifetime, duplicate keys and noncanonical encodings", () => {
  const now = 1900000000000;
  const good = `#k=${toBase64Url(invite.capability)}&s=${invite.session}&g=7&e=${now / 1000 + 300}`;
  assert.equal(readInvite(good, now).generation, 7);
  for (const value of [good + "&k=x", good + "&other=x", good.replace("g=7", "g=0"), good.replace("g=7", "g=4294967296"), good.replace("g=7", "g=7.0"), good.replace(String(now / 1000 + 300), String(now / 1000)), good.replace(String(now / 1000 + 300), String(now / 1000 + 700))]) assert.throws(() => readInvite(value, now));
  assert.throws(() => readInvite(good.replace(toBase64Url(invite.capability), toBase64Url(new Uint8Array(32))), now));
  assert.throws(() => readInvite(good.replace(invite.session, "00".repeat(32)), now));
});
test("sealed offer pin, nonce, generation, session and expiry checked before SDP is accepted", () => {
  assert.equal(validateOffer(invite, offer, 1900000000000), offer);
  for (const changed of [{ generation: 8 }, { session: "ab".repeat(32) }, { expires_at: 1900000000 }, { nonce: "bad" }, { host_pin: "ee".repeat(32) }, { guest_pin: vector.guest_pin }, { unexpected: true }]) assert.throws(() => validateOffer(invite, { ...offer, ...changed }, 1900000000000));
  assert.throws(() => sdpFingerprint(sdp + `a=fingerprint:sha-256 ${fingerprint(vector.guest_pin)}\r\n`), /Conflicting/);
  assert.throws(() => sdpFingerprint(sdp.replace("sha-256", "sha-1")), /Unsupported/);
});
test("candidate policy rejects private, host, documentation, mDNS, IPv6 and unlisted relay addresses", () => {
  for (const ip of ["0.0.0.0", "10.0.0.1", "100.64.0.1", "127.0.0.1", "169.254.1.1", "172.16.0.1", "192.168.1.1", "192.0.2.1", "192.88.99.1", "198.18.0.1", "198.51.100.1", "203.0.113.1", "224.0.0.1", "255.255.255.255", "private.local", "::1", "01.2.3.4"]) { assert.equal(publicIPv4(ip), false, ip); assert.equal(candidateAllowed(candidate(ip)), false, ip); }
  assert.equal(candidateAllowed(candidate("8.8.8.8")), true);
  assert.equal(candidateAllowed(candidate("8.8.8.8", "host")), false);
  assert.equal(candidateAllowed(candidate("8.8.8.8", "relay")), false);
  assert.equal(candidateAllowed(candidate("141.101.90.1", "relay")), true);
  assert.equal(candidateAllowed(candidate("8.8.8.8").replace("udp", "tcp")), false);
  assert.throws(() => policySdp(sdp + candidate("10.0.0.1", "host"), { remote: true }), /disallowed/);
  assert.doesNotMatch(policySdp(sdp + candidate("10.0.0.1", "host")), /10\.0\.0\.1/);
  assert.throws(() => policySdp(candidate("10.0.0.1", "host")), /No permitted/);
  assert.throws(() => policySdp((candidate("8.8.8.8") + "\r\n").repeat(33)), /Too many/);
});
test("TURN validation requires web scope, exact provider, TLS fallback, bounded lifetime and quota", () => {
  assert.equal(validateCredentials(credentials()).iceServers.length, 1);
  for (const changed of [{ client: undefined }, { ttl: 3601 }, { ttl: 0 }, { quota_bytes: SESSION_BYTES + 1 }, { allowed_relay_ips: ["8.8.8.8"] }, { iceServers: [{ urls: "turn:evil.example:3478", username: "x", credential: "x" }] }, { iceServers: [{ urls: "stun:stun.cloudflare.com:3478" }] }]) assert.throws(() => validateCredentials({ ...credentials(), ...changed }));
});
test("music SDP sets stereo, bitrate, FEC, packet time and capture warnings retain processing off", async () => {
  const result = musicSdp(sdp);
  for (const field of ["stereo=1", "sprop-stereo=1", "maxaveragebitrate=128000", "usedtx=0", "useinbandfec=1", "a=ptime:20"]) assert.ok(result.includes(field));
  assert.equal(musicSdp(result), result);
  assert.deepEqual(constraintWarnings(MUSIC_CONSTRAINTS), []);
  assert.equal(constraintWarnings({ ...MUSIC_CONSTRAINTS, channelCount: 1 }).length, 1);
  const requests = [];
  const stream = { getAudioTracks: () => [{ getSettings: () => MUSIC_CONSTRAINTS }] };
  const capture = await acquireMusicStream({ getUserMedia: async (request) => { requests.push(request); if (requests.length === 1) throw new DOMException("Unsupported", "OverconstrainedError"); return stream; } });
  assert.equal(capture.warnings.length, 1);
  assert.equal(requests[1].audio.echoCancellation, false);
  assert.equal(latencyMs(new Map()), null);
  assert.equal(latencyMs(new Map([["a", { type: "candidate-pair", nominated: true, state: "succeeded", currentRoundTripTime: .04 }], ["b", { type: "inbound-rtp", kind: "audio", jitterBufferDelay: 2, jitterBufferEmittedCount: 100 }]]), .01), 50);
});
test("bounded mailbox chunks round trip and HTTP reveals only derived addresses and ciphertext", async () => {
  const slots = new Map(), requests = [];
  const mailbox = new Mailbox("https://relay.example", invite, { interval: 0, fetchImpl: async (url, options) => {
    requests.push({ url, options });
    if (options.method === "POST") { assert.equal(slots.has(url), false); slots.set(url, JSON.parse(options.body)); return Response.json({}, { status: 201 }); }
    return Response.json(slots.get(url));
  } });
  const envelope = await seal(invite, { ...offer, sdp: sdp + "a=x:private-description\r\n".repeat(160) });
  await mailbox.send("offer", envelope);
  assert.equal(slots.size, 2);
  assert.deepEqual(await mailbox.receive("offer"), envelope);
  assert.deepEqual(await open(invite, await mailbox.receive("offer")), { ...offer, sdp: sdp + "a=x:private-description\r\n".repeat(160) });
  for (const { url, options } of requests) {
    assert.equal(options.credentials, "omit"); assert.equal(options.referrerPolicy, "no-referrer"); assert.equal(options.redirect, "error");
    const sent = url + (options.body || "");
    for (const secret of [toBase64Url(invite.capability), invite.session, "private-description"]) assert.equal(sent.includes(secret), false);
    assert.match(url, /\/mailbox\/[a-f0-9]{64}\/offer\?client=web$/);
  }
});
test("malformed chunk count fails before amplified reads; quota/outage errors fail closed", async () => {
  let calls = 0;
  const malformed = new Mailbox("https://relay.example", invite, { interval: 0, fetchImpl: async () => { calls++; return Response.json({ payload: toBase64Url(new Uint8Array([0, 255, 1])) }); } });
  await assert.rejects(malformed.receive("offer"), /chunk/); assert.equal(calls, 1);
  for (const status of [429, 503]) {
    const blocked = new Mailbox("https://relay.example", invite, { interval: 0, fetchImpl: async () => new Response("<html>busy</html>", { status }) });
    await assert.rejects(blocked.credentials(), { message: BUSY_MESSAGE });
  }
});
test("mailbox request timeout and leave cancel pending network work", async () => {
  const controller = new AbortController();
  const mailbox = new Mailbox("https://relay.example", invite, { signal: controller.signal, interval: 0, timeout: 10, fetchImpl: async (_url, { signal }) => new Promise((_, reject) => { signal.addEventListener("abort", () => reject(new DOMException("Stopped", "AbortError")), { once: true }); }) });
  await assert.rejects(mailbox.request("/test"), { name: "AbortError" });
  controller.abort(); await assert.rejects(mailbox.request("/test"), { name: "AbortError" });
});
test("mailbox rejects oversized streaming and declared responses before parsing", async () => {
  for (const response of [new Response("x".repeat(16385)), new Response("{}", { headers: { "content-length": "999999" } })]) {
    const mailbox = new Mailbox("https://relay.example", invite, { interval: 0, fetchImpl: async () => response });
    await assert.rejects(mailbox.request("/test"), /exceeds the limit/);
  }
});
test("session quotas count every transport, fail on unavailable/decreasing counters, and stop tracks", async () => {
  for (const stats of [[], [{ type: "transport", bytesSent: -1, bytesReceived: 0 }], [{ type: "transport", bytesSent: 0 }], [{ type: "transport", bytesSent: SESSION_BYTES, bytesReceived: 0 }]]) {
    let stops = 0, closes = 0; const errors = [];
    const track = { enabled: true, stop: () => stops++ };
    const session = new WebJoinSession({ origin: "https://relay.example", invite, stream: { getAudioTracks: () => [track], getTracks: () => [track] }, audioContext: { baseLatency: 0 }, onError: (error) => errors.push(error) });
    assert.equal(track.enabled, false);
    session.authorized = true;
    session.pc = { getStats: async () => new Map(stats.map((item, i) => [i, item])), close: () => closes++ };
    await session.updateStats();
    assert.equal(errors.length, 1); assert.equal(stops, 1); assert.equal(closes, 1); assert.equal(session.controller.signal.aborted, true);
  }
});
