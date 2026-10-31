import { test, expect } from "@playwright/test";
import { toBase64Url, fromHex, seal } from "../../crypto.js";
const invite = () => ({ capability: fromHex("11".repeat(32)), session: "22".repeat(32), generation: 1, expiresAt: Math.floor(Date.now() / 1000) + 300 });
const fragment = (value) => `#k=${toBase64Url(value.capability)}&s=${value.session}&g=${value.generation}&e=${value.expiresAt}`;
async function fakeAudio(page, nativeMockCapture = false) {
  await page.addInitScript((nativeMockCapture) => {
    window.captureCalls = 0; window.captureTracks = []; window.captureContexts = [];
    const prototype = Object.getPrototypeOf(navigator.mediaDevices);
    const nativeCapture = prototype.getUserMedia;
    Object.defineProperty(prototype, "getUserMedia", { configurable: true, value: async (constraints) => {
      window.captureCalls++;
      if (nativeMockCapture) {
        const stream = await nativeCapture.call(navigator.mediaDevices, constraints);
        window.captureTracks.push(...stream.getTracks()); return stream;
      }
      const context = new AudioContext({ sampleRate: 48000 }); await context.resume();
      window.captureContexts.push(context);
      const oscillator = context.createOscillator(), gain = context.createGain(), output = context.createMediaStreamDestination();
      oscillator.frequency.value = 440; gain.gain.value = .02; oscillator.connect(gain).connect(output); oscillator.start();
      window.captureTracks.push(...output.stream.getTracks()); return output.stream;
    } });
  }, nativeMockCapture);
}
test("headphones gate capture; encrypted offer pin rejection closes fake sine capture", async ({ page, browserName }) => {
  await fakeAudio(page, browserName === "webkit");
  const value = invite();
  const badOffer = { v: 1, kind: "offer", session: value.session, generation: value.generation, expires_at: value.expiresAt, nonce: "33".repeat(16), host_pin: "44".repeat(32), guest_pin: "", sdp: `v=0\r\na=fingerprint:sha-256 ${Array(32).fill("55").join(":")}\r\n` };
  const envelope = await seal(value, badOffer);
  const requests = [];
  await page.route("https://webjam-turn-credentials.jeffstory007.workers.dev/**", async (route) => {
    requests.push(route.request());
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ payload: toBase64Url(new Uint8Array([0, 1, ...envelope])) }) });
  });
  await page.goto(`/${fragment(value)}`);
  await expect(page.getByRole("button", { name: "Join with microphone" })).toBeDisabled();
  await expect(page.getByText("Web Join, higher delay (about 60–120 ms).", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => window.captureCalls)).toBe(0);
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Join with microphone" }).click();
  await expect(page.locator("#status")).toContainText("Host identity or invite binding did not match");
  expect(await page.evaluate(() => window.captureCalls)).toBe(1);
  expect(await page.evaluate(() => window.captureTracks.every((track) => track.readyState === "ended"))).toBe(true);
  expect(requests.length).toBe(1);
  expect(requests[0].url()).not.toContain(toBase64Url(value.capability));
});
test("actual browser ephemeral certificates and WebCrypto produce the shared Go proof", async ({ page }) => {
  await page.goto("/");
  const result = await page.evaluate(async () => {
    const cryptoModule = await import("/crypto.js");
    const vector = await (await fetch("/test/vectors.json")).json();
    const certificate = await RTCPeerConnection.generateCertificate({ name: "ECDSA", namedCurve: "P-256" });
    const pc = new RTCPeerConnection({ certificates: [certificate], iceServers: [] });
    pc.addTransceiver("audio");
    const offer = await pc.createOffer();
    const pin = cryptoModule.sdpFingerprint(offer.sdp);
    pc.close();
    const value = { capability: cryptoModule.fromHex(vector.capability_hex), session: vector.session, generation: vector.generation, expiresAt: vector.expires_at };
    return { pin, proof: cryptoModule.toHex(await cryptoModule.enrollmentProof(value, vector.answer)), expected: vector.proof_hex, opened: await cryptoModule.open(value, cryptoModule.fromBase64Url(vector.envelope_b64url)), answer: vector.answer };
  });
  expect(result.pin).toMatch(/^[0-9a-f]{64}$/);
  expect(result.proof).toBe(result.expected);
  expect(result.opened).toEqual(result.answer);
});

test("real browser sine reaches Pion after enrollment and returns through the host lab bridge", async ({ page, request, browserName }) => {
  await fakeAudio(page, browserName === "webkit");
  const response = await request.get("http://127.0.0.1:4175/offer");
  expect(response.ok()).toBe(true);
  const host = await response.json();
  await page.goto("/");
  await page.evaluate((host) => {
    window.labHost = host;
    const button = document.createElement("button"); button.id = "lab-start"; button.textContent = "Start local audio lab";
    document.body.append(button);
    button.addEventListener("click", () => {
      window.labAnswer = (async () => {
        const crypt = await import("/crypto.js");
        const audio = await import("/audio.js");
        const invite = { capability: crypt.fromHex(host.capability_hex), session: host.session, generation: host.generation, expiresAt: host.expires_at };
        const offer = crypt.validateOffer(invite, await crypt.open(invite, crypt.fromBase64Url(host.offer_b64url)));
        const certificate = await RTCPeerConnection.generateCertificate({ name: "ECDSA", namedCurve: "P-256" });
        const pc = window.labPC = new RTCPeerConnection({ certificates: [certificate], iceServers: [] });
        const { stream } = await audio.acquireMusicStream(navigator.mediaDevices);
        window.labCapture = stream;
        stream.getAudioTracks()[0].enabled = false;
        const audioContext = window.labContext = new AudioContext({ sampleRate: 48000 }); await audioContext.resume();
        let answer;
        window.labAuthorized = new Promise((resolve, reject) => {
          pc.ondatachannel = (event) => {
            const channel = event.channel;
            if (channel.label !== "webjoin-enrollment" || !channel.ordered || channel.maxRetransmits !== null || channel.maxPacketLifeTime !== null) { reject(new Error("Unexpected channel")); return; }
            let sent = false;
            const prove = async () => { if (sent) return; sent = true; channel.send(await crypt.enrollmentProof(invite, answer)); };
            channel.onopen = prove;
            if (channel.readyState === "open") prove();
            channel.onmessage = (event) => {
              if (event.data !== "authorized") { reject(new Error("Enrollment rejected")); return; }
              stream.getAudioTracks()[0].enabled = true;
              resolve();
            };
          };
        });
        pc.ontrack = (event) => {
          const remote = event.streams[0] || new MediaStream([event.track]);
          // Match production playback: Chromium needs an active media element
          // to pull the remote decoder before a WebAudio analyser can meter it.
          const playback = document.getElementById("host-audio");
          playback.srcObject = remote; playback.volume = 0;
          playback.play().catch((error) => { window.labPlaybackError = error.message; });
          const analyser = window.labAnalyser = audioContext.createAnalyser();
          audioContext.createMediaStreamSource(remote).connect(analyser);
          const silentOutput = audioContext.createGain(); silentOutput.gain.value = 0;
          analyser.connect(silentOutput).connect(audioContext.destination);
        };
        // This test-only host is loopback. Production policySdp rejects these
        // candidates; crypto, certificates, DTLS, SRTP and Opus remain real.
        await pc.setRemoteDescription({ type: "offer", sdp: offer.sdp });
        pc.addTrack(stream.getAudioTracks()[0], stream);
        const description = await pc.createAnswer();
        await pc.setLocalDescription({ type: "answer", sdp: audio.musicSdp(description.sdp) });
        if (pc.iceGatheringState !== "complete") await new Promise((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error("Lab ICE gathering timed out")), 15000);
          pc.onicegatheringstatechange = () => { if (pc.iceGatheringState === "complete") { clearTimeout(timer); resolve(); } };
        });
        const sdp = pc.localDescription.sdp.split(/\r?\n/).filter((line) => !line.startsWith("a=candidate:") || (line.split(" ")[1] === "1" && line.split(" ")[2].toLowerCase() === "udp")).join("\r\n");
        answer = { ...offer, kind: "answer", guest_pin: crypt.sdpFingerprint(sdp), sdp };
        return crypt.toBase64Url(await crypt.seal(invite, answer));
      })();
    });
  }, host);
  await page.locator("#lab-start").click();
  const envelope = await page.evaluate(() => window.labAnswer);
  const applied = await request.post("http://127.0.0.1:4175/answer", { data: { envelope_b64url: envelope } });
  expect(applied.ok(), await applied.text()).toBe(true);
  await expect.poll(async () => {
    const status = await (await request.get("http://127.0.0.1:4175/status")).json();
    if (status.error) throw new Error(status.error);
    return status.authorized;
  }).toBe(true);
  await page.evaluate(() => window.labAuthorized);
  try {
    await expect.poll(() => page.evaluate(() => {
      if (!window.labAnalyser) return 0;
      const samples = new Float32Array(window.labAnalyser.fftSize);
      window.labAnalyser.getFloatTimeDomainData(samples);
      return Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
    })).toBeGreaterThan(.001);
  } catch (error) {
    const status = await (await request.get("http://127.0.0.1:4175/status")).json();
    const browser = await page.evaluate(async () => ({ state: window.labPC.connectionState, audio: window.labContext.state, tracks: window.labCapture.getTracks().map((track) => ({ enabled: track.enabled, muted: track.muted, state: track.readyState })), counters: [...(await window.labPC.getStats()).values()].filter((row) => ["inbound-rtp", "outbound-rtp"].includes(row.type)).map((row) => ({ type: row.type, bytesReceived: row.bytesReceived, bytesSent: row.bytesSent, packetsReceived: row.packetsReceived, totalAudioEnergy: row.totalAudioEnergy, jitterBufferEmittedCount: row.jitterBufferEmittedCount })) }));
    throw new Error(`${error.message}\nLab host: ${JSON.stringify(status)}\nBrowser: ${JSON.stringify(browser)}`);
  }
  const stats = await (await request.get("http://127.0.0.1:4175/status")).json();
  expect(stats.received).toBeGreaterThan(0); expect(stats.sent).toBeGreaterThan(0);
  await page.evaluate(async () => { window.labPC.close(); window.labCapture.getTracks().forEach((track) => track.stop()); await window.labContext.close(); });
});
