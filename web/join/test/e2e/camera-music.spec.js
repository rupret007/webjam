import { test, expect } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import { fromBase64Url, fromHex, mailboxId, open, seal, toBase64Url } from "../../crypto.js";

const issuer = "https://webjam-turn-credentials.jeffstory007.workers.dev";
const fixtureAddress = "8.8.8.8";

async function syntheticDevices(page) {
  await page.addInitScript(address => {
    // Only the loopback lab substitutes candidate addresses. Exercise the
    // shipping filter, encryption, negotiation and media with no external ICE.
    const native = RTCPeerConnection;
    const localDescription = Object.getOwnPropertyDescriptor(native.prototype, "localDescription").get;
    const remoteDescription = native.prototype.setRemoteDescription;
    const peerConfiguration = configuration => ({ ...configuration, iceServers: [] });
    const rewriteCandidates = (sdp, rewrite) => sdp.replace(/^a=candidate:[^\r\n]*/gm, line => {
      const fields = line.split(" ");
      return fields[1] === "1" && fields[2].toLowerCase() === "udp" ? rewrite(fields).join(" ") : line;
    });
    window.labPeers = [];
    window.RTCPeerConnection = class extends native {
      constructor(configuration) {
        super(peerConfiguration(configuration));
        window.labPeers.push(this);
      }
      get localDescription() {
        const description = localDescription.call(this);
        if (!description) return description;
        const sdp = rewriteCandidates(description.sdp, fields => {
          // Ports are unique per ICE transport. The two pages share this map.
          localStorage.setItem("lab-candidate-" + fields[5], fields.slice(4).join(" "));
          return [...fields.slice(0, 4), address, fields[5], "typ", "srflx"];
        });
        return { type: description.type, sdp };
      }
      setRemoteDescription(description) {
        const sdp = rewriteCandidates(description.sdp, fields => {
          if (fields[4] !== address) return fields;
          const original = localStorage.getItem("lab-candidate-" + fields[5]);
          if (!original) throw new Error("Loopback candidate was not mapped");
          return [...fields.slice(0, 4), ...original.split(" ")];
        });
        return remoteDescription.call(this, { type: description.type, sdp });
      }
    };
    window.labCameraCalls = 0;
    window.labCameraTracks = [];
    const contexts = [];
    const frameTimers = [];
    Object.defineProperty(navigator.mediaDevices, "enumerateDevices", {
      value: async () => [{ kind: "videoinput", deviceId: "lab-camera-2" }],
    });
    Object.defineProperty(navigator.mediaDevices, "getUserMedia", {
      value: async ({ video, audio }) => {
        if (video) {
          if (audio) throw new Error("Camera must not request audio");
          const canvas = document.createElement("canvas");
          canvas.width = 640;
          canvas.height = 360;
          const context = canvas.getContext("2d");
          const color = location.search ? "#346b60" : "#465b87";
          const switched = ++window.labCameraCalls > 1;
          const draw = () => {
            context.fillStyle = switched ? "#725b83" : color;
            context.fillRect(0, 0, canvas.width, canvas.height);
            context.fillStyle = "#e5ede8";
            context.beginPath();
            context.arc(320, 145, 49, 0, Math.PI * 2);
            context.fill();
            context.beginPath();
            context.ellipse(320, 267, 110, 77, 0, 0, Math.PI * 2);
            context.fill();
          };
          draw();
          frameTimers.push(setInterval(draw, 66));
          const stream = canvas.captureStream(15);
          window.labCameraTracks.push(stream.getVideoTracks()[0]);
          return stream;
        }
        const context = new AudioContext({ sampleRate: 48000 });
        contexts.push(context);
        await context.resume();
        const oscillator = context.createOscillator();
        const gain = context.createGain();
        const output = context.createMediaStreamDestination();
        oscillator.frequency.value = 440;
        gain.gain.value = 0.02;
        oscillator.connect(gain).connect(output);
        oscillator.start();
        return output.stream;
      },
    });
    addEventListener("pagehide", () => {
      frameTimers.forEach(clearInterval);
      contexts.forEach(context => void context.close());
      localStorage.clear();
    }, { once: true });
    addEventListener("DOMContentLoaded", () => { document.querySelector("#host-audio").volume = 0; }, { once: true });
  }, fixtureAddress);
}

async function fixtureRelay(browserContext, page, baseURL, request, host, invite) {
  const musicPath = kind => mailboxId(invite, kind, 0).then(id => "/mailbox/" + id + "/" + kind);
  const [offerPath, answerPath] = await Promise.all([musicPath("offer"), musicPath("answer")]);
  const offer = await open(invite, fromBase64Url(host.offer_b64url));
  offer.sdp = offer.sdp.replace(/^a=candidate:[^\r\n]*/gm, line => {
    const fields = line.split(" ");
    return [...fields.slice(0, 4), fixtureAddress, fields[5], "typ", "srflx"].join(" ");
  });
  const envelope = await seal(invite, offer);
  const stored = new Map([[offerPath, toBase64Url(new Uint8Array([0, 1, ...envelope]))]]);
  const cors = { "Access-Control-Allow-Origin": baseURL, "Access-Control-Allow-Headers": "content-type" };
  await browserContext.route("**/*", async route => {
    const { origin, pathname } = new URL(route.request().url());
    if (origin === baseURL) return route.continue();
    if (origin !== issuer) return route.abort();
    const method = route.request().method();
    if (method === "OPTIONS") return route.fulfill({ status: 204, headers: { ...cors, "Access-Control-Allow-Methods": "GET, POST" } });
    const respond = (status, json) => route.fulfill({ status, headers: cors, ...(json ? { json } : {}) });
    if (pathname === "/turn-credentials") {
      const { client } = route.request().postDataJSON();
      expect(["web", "web-video"]).toContain(client);
      return respond(200, { client, ttl: 600, quota_bytes: (client === "web-video" ? 64 : 256) * 1024 * 1024,
        iceServers: [{ urls: "turn:turn.cloudflare.com:3478?transport=udp", username: "fixture", credential: "fixture" }] });
    }
    if (method === "GET") {
      const payload = stored.get(pathname);
      return payload ? respond(200, { payload }) : respond(204);
    }
    const { payload } = route.request().postDataJSON();
    if (stored.has(pathname)) return respond(409, { error: "duplicate" });
    if (pathname === answerPath) {
      const chunk = fromBase64Url(payload);
      expect(Array.from(chunk.subarray(0, 2))).toEqual([0, 1]);
      const answer = await open(invite, chunk.subarray(2));
      answer.sdp = await page.evaluate(sdp => sdp.replace(/^a=candidate:[^\r\n]*/gm, line => {
        const fields = line.split(" ");
        const candidate = localStorage.getItem("lab-candidate-" + fields[5]);
        if (!candidate) throw new Error("Loopback audio candidate was not mapped");
        return [...fields.slice(0, 4), candidate].join(" ");
      }), answer.sdp);
      const nativeAnswer = await seal(invite, answer);
      const result = await request.post("http://127.0.0.1:4175/answer", {
        data: { envelope_b64url: toBase64Url(nativeAnswer) },
      });
      expect(result.status()).toBe(204);
    } else {
      expect(pathname).toMatch(/^\/video-mailbox\/[a-f0-9]{64}\/(offer|answer)$/);
    }
    stored.set(pathname, payload);
    return respond(201, {});
  });
  return offer;
}

function decoded(page) {
  return page.evaluate(async () => {
    const peer = window.labPeers.at(-1);
    const inbound = [...(await peer.getStats()).values()].find(row => row.type === "inbound-rtp" && row.kind === "video");
    return inbound?.framesDecoded ?? 0;
  });
}

async function musicWindow(page) {
  return page.evaluate(async () => {
    const { latencyMs } = await import("/audio.js");
    const pc = window.labPeers[0];
    const values = [];
    for (let i = 0; i < 20; i++) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      if (pc.connectionState !== "connected") throw new Error("Music ended during the timing window");
      const ms = latencyMs(await pc.getStats(), window.labMusicBaseLatency);
      if (!Number.isFinite(ms)) throw new Error("Expected finite music timing");
      values.push(ms);
    }
    values.sort((a, b) => a - b);
    return { samples: values.length, medianMs: (values[9] + values[10]) / 2 };
  });
}

test("WebJam camera and music run on separate real peers in the local lab", async ({ page, context, request, baseURL }, info) => {
  test.setTimeout(100000);
  const offerResponse = await request.get("http://127.0.0.1:4175/offer?journey=camera-music");
  expect(offerResponse.ok()).toBe(true);
  const nativeHost = await offerResponse.json();
  const value = { capability: fromHex(nativeHost.capability_hex), session: nativeHost.session,
    generation: nativeHost.generation, expiresAt: nativeHost.expires_at };
  const fragment = `#k=${toBase64Url(value.capability)}&s=${value.session}&g=${value.generation}&e=${value.expiresAt}`;
  const offer = await fixtureRelay(context, page, baseURL, request, nativeHost, value);
  const host = await context.newPage();
  await syntheticDevices(page);
  await syntheticDevices(host);
  await page.addInitScript(({ sdp, address }) => {
    // The native lab listens on loopback and supplies only host candidates.
    for (const line of sdp.split(/\r?\n/).filter(line => line.startsWith("a=candidate:"))) {
      const fields = line.split(" ");
      if (fields[4] === address) localStorage.setItem("lab-candidate-" + fields[5], "127.0.0.1 " + fields[5] + " typ host");
    }
    const Original = AudioContext;
    window.AudioContext = class extends Original {
      constructor(...args) {
        super(...args);
        window.labMusicBaseLatency ??= this.baseLatency;
      }
    };
  }, { sdp: offer.sdp, address: fixtureAddress });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/${fragment}`);
  await host.goto(`/?cameras=1${fragment}`);
  await page.bringToFront();
  await page.getByRole("button", { name: "Skip check" }).click();
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Join with microphone" }).click();
  const guestShow = page.getByRole("button", { name: "Show camera", exact: true });
  await expect(guestShow).toBeEnabled();
  expect(await page.evaluate(() => [window.labPeers.length, window.labCameraCalls])).toEqual([1, 0]);
  expect(await host.evaluate(() => [window.labPeers.length, window.labCameraCalls])).toEqual([0, 0]);
  await guestShow.click();
  await expect(page.locator("[data-video-status]")).toContainText("Music timing isn't measurable");
  const before = await musicWindow(page);
  await guestShow.click();
  await expect(page.locator("[data-self]")).toBeVisible();
  await expect(page.locator("[data-remote]")).toBeHidden();
  await page.screenshot({ path: info.outputPath("camera-preview-guest-390.png") });
  await host.getByRole("button", { name: "Show my camera" }).click();
  // Keep the page producing the synthetic microphone foregrounded.
  await page.bringToFront();
  for (const peer of [page, host]) {
    await expect(peer.locator("[data-video-status]")).toHaveText("Cameras on.", { timeout: 20000 });
    await expect.poll(() => decoded(peer)).toBeGreaterThan(0);
  }
  expect(await page.evaluate(() => window.labPeers.map(peer =>
    peer.getSenders().map(sender => sender.track?.kind).filter(Boolean)))).toEqual([["audio"], ["video"]]);
  expect(await host.evaluate(() => window.labPeers.map(peer =>
    peer.getSenders().map(sender => sender.track?.kind).filter(Boolean)))).toEqual([["video"]]);
  const phone = { width: 390, height: 844 };
  const desktop = { width: 1440, height: 900 };
  for (const size of [phone, desktop]) {
    await page.setViewportSize(size);
    const stage = await page.locator("[data-remote]").boundingBox();
    const preview = await page.locator("[data-self]").boundingBox();
    expect(stage.width * stage.height).toBeGreaterThan(preview.width * preview.height);
    expect(stage.y + stage.height).toBeLessThanOrEqual(preview.y);
    const controlRows = [];
    for (const name of ["Hide camera", "Switch camera", "Leave"]) {
      const control = page.getByRole("button", { name, exact: true });
      await expect(control).toBeInViewport({ ratio: 1 });
      const box = await control.boundingBox();
      expect(box.height).toBeGreaterThanOrEqual(44);
      controlRows.push(box.y);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    expect(new Set(controlRows).size).toBe(1);
    const status = page.locator("#status");
    await expect(status).toHaveText("Connected to music");
    expect(await status.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath(`camera-and-music-${size.width}.png`) });
  }
  const after = await musicWindow(page);
  expect(after.medianMs - before.medianMs).toBeLessThanOrEqual(10);
  const timingPath = info.outputPath("music-timing.json");
  const differenceMs = after.medianMs - before.medianMs;
  await writeFile(timingPath, JSON.stringify({ before, after, differenceMs }, null, 2));
  await info.attach("music timing (loopback)", { path: timingPath, contentType: "application/json" });
  const beforeSwitch = await decoded(host);
  const musicLab = await (await request.get("http://127.0.0.1:4175/status")).json();
  const cameraStatus = (await page.locator("[data-video-status]").textContent())?.trim();
  expect(musicLab.authorized, `Pion fixture state: ${musicLab.error || "no host error"}`).toBe(true);
  await expect(page.getByRole("button", { name: "Switch camera" }), cameraStatus).toBeVisible();
  await page.getByRole("button", { name: "Switch camera" }).click();
  await expect.poll(() => page.evaluate(() => window.labCameraTracks.map(track => track.readyState))).toEqual(["ended", "live"]);
  await expect.poll(() => decoded(host)).toBeGreaterThan(beforeSwitch);
  await page.getByRole("button", { name: "Hide camera" }).click();
  expect(await page.evaluate(() => window.labCameraTracks.at(-1).enabled)).toBe(false);
  await guestShow.click();
  expect(await page.evaluate(() => [window.labPeers.length, window.labCameraCalls])).toEqual([2, 2]);
  // Simulate a newly selected music relay only in browser stats. The music
  // continues through the lab bridge, so guard behavior is observable.
  await page.evaluate(() => {
    const music = window.labPeers[0];
    const getStats = music.getStats.bind(music);
    music.getStats = async () => {
      const rows = new Map(await getStats());
      const transport = [...rows.values()].find(row => row.type === "transport");
      const pair = rows.get(transport?.selectedCandidatePairId);
      if (!pair) throw new Error("No selected music candidate pair");
      rows.set(transport.id, { ...transport, selectedCandidatePairId: "guard-pair" });
      rows.set("guard-pair", { ...pair, id: "guard-pair", nominated: true, localCandidateId: "guard-relay" });
      rows.set("guard-relay", { type: "local-candidate", candidateType: "relay" });
      return rows;
    };
  });
  await expect(page.locator("[data-video-status]")).toHaveText("Video paused to protect music timing. Music stays connected.");
  await expect(page.getByRole("button", { name: "Leave", exact: true })).toBeInViewport({ ratio: 1 });
  expect(await page.evaluate(() => window.labPeers.map(pc => pc.connectionState))).toEqual(["connected", "closed"]);
  const musicState = await (await request.get("http://127.0.0.1:4175/status")).json();
  expect(musicState.authorized).toBe(true);
  expect(musicState.received).toBeGreaterThan(0);
  expect(musicState.sent).toBeGreaterThan(0);
  await page.screenshot({ path: info.outputPath("video-paused-music-connected.png") });
  await page.getByRole("button", { name: "Leave", exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.labPeers.map(pc => pc.connectionState))).toEqual(["closed", "closed"]);
  await host.close();
});
