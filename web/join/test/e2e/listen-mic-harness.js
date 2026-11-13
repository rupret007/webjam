import { fromHex, mailboxId, open, sdpFingerprint, toBase64Url, fromBase64Url } from "/crypto.js";
import { WebJoinSession } from "/session.js";

const CHUNK = 2998;

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function chunkPayloads(bytes) {
  const count = Math.max(1, Math.ceil(bytes.length / CHUNK));
  const payloads = [];
  for (let index = 0; index < count; index += 1) {
    const slice = bytes.subarray(index * CHUNK, (index + 1) * CHUNK);
    const wrapped = new Uint8Array(slice.length + 2);
    wrapped[0] = index;
    wrapped[1] = count;
    wrapped.set(slice, 2);
    payloads.push(toBase64Url(wrapped));
  }
  return payloads;
}

function iceComplete(pc) {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("ICE gathering timed out")), 8000);
    pc.addEventListener("icegatheringstatechange", () => {
      if (pc.iceGatheringState === "complete") {
        clearTimeout(timer);
        resolve();
      }
    });
  });
}

async function proofBytes(data) {
  if (data instanceof ArrayBuffer) return new Uint8Array(data);
  if (ArrayBuffer.isView(data)) return new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
  if (typeof Blob !== "undefined" && data instanceof Blob) return new Uint8Array(await data.arrayBuffer());
  return null;
}

async function rtp(pc, type) {
  const stats = await pc.getStats();
  const packetsKey = type === "inbound-rtp" ? "packetsReceived" : "packetsSent";
  const bytesKey = type === "inbound-rtp" ? "bytesReceived" : "bytesSent";
  let packets = 0;
  let bytes = 0;
  for (const report of stats.values()) {
    if (report.type !== type || (report.kind !== "audio" && report.mediaType !== "audio")) continue;
    packets += Number(report[packetsKey]) || 0;
    bytes += Number(report[bytesKey]) || 0;
  }
  return { packets, bytes };
}

function installCaptureSpy() {
  let calls = 0;
  const media = Object.getPrototypeOf(navigator.mediaDevices);
  const native = media.getUserMedia;
  Object.defineProperty(media, "getUserMedia", {
    configurable: true,
    value(...args) {
      calls += 1;
      return native.apply(this, args);
    },
  });
  return () => calls;
}

async function fakeMicrophone(browserName) {
  if (browserName === "chromium") {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    return { stream, label: stream.getAudioTracks()[0]?.label || "", context: null };
  }
  const context = new AudioContext({ sampleRate: 48000 });
  await context.resume();
  const oscillator = context.createOscillator();
  const gain = context.createGain();
  const output = context.createMediaStreamDestination();
  oscillator.frequency.value = 440;
  gain.gain.value = 0.25;
  oscillator.connect(gain).connect(output);
  oscillator.start();
  return { stream: output.stream, label: "generated", context };
}

async function silenceThenAttach(session, browserName, remotePackets) {
  const guest = session.pc;
  const captureCalls = session.captureCalls;
  const joinedTransceiver = guest.getTransceivers().find((item) => item.receiver?.track?.kind === "audio");
  await new Promise((resolve) => setTimeout(resolve, 800));
  const beforePackets = await remotePackets();
  const before = {
    captureCallsAtJoin: captureCalls(),
    sendingTracksAtJoin: guest.getSenders().filter((sender) => sender.track).length,
    outboundAtJoin: await rtp(guest, "outbound-rtp"),
    inboundAtJoin: beforePackets,
    directionAtJoin: joinedTransceiver?.currentDirection || "",
    audioSections: (guest.localDescription.sdp.match(/^m=audio /gm) || []).length,
  };
  const mic = await fakeMicrophone(browserName);
  const attached = await session.attachMicrophone(mic.stream);
  let inboundAfter = await remotePackets();
  const deadline = Date.now() + 3000;
  while (inboundAfter.packets <= before.inboundAtJoin.packets && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 100));
    inboundAfter = await remotePackets();
  }
  return {
    before,
    mic,
    result: {
      ...before,
      attached,
      captureCallsAfter: captureCalls(),
      inboundAfter,
      outboundAfter: await rtp(guest, "outbound-rtp"),
      directionAfter: joinedTransceiver?.currentDirection || "",
      signalingStable: guest.signalingState === "stable",
      trackLabel: mic.label,
    },
  };
}

export async function runPionMicProof(browserName, lab) {
  const captureCalls = installCaptureSpy();
  const invite = {
    capability: fromHex(lab.capability_hex),
    session: lab.session,
    generation: lab.generation,
    expiresAt: lab.expires_at,
  };
  const errors = [];
  let session;
  let mic;
  try {
    const offerPayloads = chunkPayloads(fromBase64Url(lab.offer_b64url));
    const offerIds = new Map();
    const answerIds = new Map();
    for (const index of [0, 1, 2]) {
      offerIds.set(await mailboxId(invite, "offer", index), index);
      answerIds.set(await mailboxId(invite, "answer", index), index);
    }
    const answerParts = new Map();
    window.fetch = async (url, init = {}) => {
      const parsed = new URL(url, location.origin);
      if (parsed.origin !== location.origin) throw new Error("blocked non-loopback request");
      const [, root, id, kind] = parsed.pathname.split("/");
      if (root !== "mailbox") throw new Error(`blocked ${parsed.pathname}`);
      if ((init.method || "GET").toUpperCase() === "GET") {
        const index = offerIds.get(id);
        if (kind !== "offer" || index === undefined || index >= offerPayloads.length) return new Response(null, { status: 204 });
        return jsonResponse({ payload: offerPayloads[index] });
      }
      if (kind !== "answer" || answerIds.get(id) === undefined) return jsonResponse({}, 404);
      const chunk = fromBase64Url(JSON.parse(init.body).payload);
      answerParts.set(chunk[0], chunk.subarray(2));
      if (chunk[0] === 0) {
        const pieces = [];
        for (let index = 0; index < chunk[1]; index += 1) {
          const piece = answerParts.get(index);
          if (!piece) throw new Error("incomplete answer");
          pieces.push(piece);
        }
        const bytes = new Uint8Array(pieces.reduce((sum, piece) => sum + piece.length, 0));
        let offset = 0;
        for (const piece of pieces) {
          bytes.set(piece, offset);
          offset += piece.length;
        }
        if (typeof window.webjamApplyAnswer !== "function") throw new Error("loopback host bridge is missing");
        const reason = await window.webjamApplyAnswer(toBase64Url(bytes));
        if (reason) throw String(reason);
      }
      return jsonResponse({}, 201);
    };
    const audioContext = new AudioContext({ sampleRate: 48000 });
    await audioContext.resume();
    session = new WebJoinSession({
      origin: location.origin,
      invite,
      stream: null,
      audioContext,
      loopbackLab: true,
      onError: (error) => errors.push(error?.message || String(error)),
    });
    session.captureCalls = captureCalls;
    await session.join();
    if (errors.length) throw new Error(errors.join("; "));
    const read = async () => {
      if (typeof window.webjamLabStatus !== "function") throw new Error("loopback host status is missing");
      const status = await window.webjamLabStatus();
      if (status.error) throw new Error("host ended the loopback session");
      return { packets: status.received || 0, bytes: status.received || 0 };
    };
    const { mic: opened, result } = await silenceThenAttach(session, browserName, read);
    mic = opened;
    return { ...result, errors: [...errors] };
  } finally {
    session?.close();
    mic?.stream?.getTracks().forEach((track) => track.stop());
    if (mic?.context) await mic.context.close();
  }
}

export async function runListenMicProof(browserName) {
  const captureCalls = installCaptureSpy();
  const invite = {
    capability: fromHex("11".repeat(32)),
    session: "22".repeat(32),
    generation: 1,
    expiresAt: Math.floor(Date.now() / 1000) + 300,
  };
  const host = new RTCPeerConnection({ iceServers: [] });
  const errors = [];
  let session;
  let mic;
  try {
    host.addTransceiver("audio", { direction: "sendrecv" });
    const channel = host.createDataChannel("webjoin-enrollment");
    channel.binaryType = "arraybuffer";
    let replied = false;
    channel.addEventListener("message", (event) => {
      void proofBytes(event.data).then((bytes) => {
        if (replied || !bytes || bytes.length !== 32) return;
        replied = true;
        channel.send("authorized");
      });
    });
    await host.setLocalDescription(await host.createOffer());
    await iceComplete(host);
    const offer = {
      v: 1,
      kind: "offer",
      session: invite.session,
      generation: invite.generation,
      expires_at: invite.expiresAt,
      nonce: "33".repeat(16),
      host_pin: sdpFingerprint(host.localDescription.sdp),
      guest_pin: "",
      sdp: host.localDescription.sdp,
    };
    const { seal } = await import("/crypto.js");
    const offerPayloads = chunkPayloads(await seal(invite, offer));
    const offerIds = new Map();
    const answerIds = new Map();
    for (const index of [0, 1, 2]) {
      offerIds.set(await mailboxId(invite, "offer", index), index);
      answerIds.set(await mailboxId(invite, "answer", index), index);
    }
    const answerParts = new Map();
    const nativeFetch = globalThis.fetch.bind(globalThis);
    window.fetch = async (url, init = {}) => {
      const parsed = new URL(url, location.origin);
      if (parsed.origin !== location.origin) {
        if (parsed.hostname === "127.0.0.1" || parsed.hostname === "localhost") return nativeFetch(url, init);
        throw new Error("blocked non-loopback request");
      }
      const [, root, id, kind] = parsed.pathname.split("/");
      if (root !== "mailbox") throw new Error(`blocked ${parsed.pathname}`);
      if ((init.method || "GET").toUpperCase() === "GET") {
        const index = offerIds.get(id);
        if (kind !== "offer" || index === undefined || index >= offerPayloads.length) return new Response(null, { status: 204 });
        return jsonResponse({ payload: offerPayloads[index] });
      }
      const indexSlot = answerIds.get(id);
      if (kind !== "answer" || indexSlot === undefined) return jsonResponse({}, 404);
      const chunk = fromBase64Url(JSON.parse(init.body).payload);
      answerParts.set(chunk[0], chunk.subarray(2));
      if (chunk[0] === 0) {
        const count = chunk[1];
        const pieces = [];
        for (let index = 0; index < count; index += 1) {
          const piece = answerParts.get(index);
          if (!piece) throw new Error("incomplete answer");
          pieces.push(piece);
        }
        const bytes = new Uint8Array(pieces.reduce((sum, piece) => sum + piece.length, 0));
        let offset = 0;
        for (const piece of pieces) {
          bytes.set(piece, offset);
          offset += piece.length;
        }
        const answer = await open(invite, bytes);
        await host.setRemoteDescription({ type: "answer", sdp: answer.sdp });
      }
      return jsonResponse({}, 201);
    };
    const audioContext = new AudioContext({ sampleRate: 48000 });
    await audioContext.resume();
    session = new WebJoinSession({
      origin: location.origin,
      invite,
      stream: null,
      audioContext,
      loopbackLab: true,
      onError: (error) => errors.push(error?.message || String(error)),
    });
    await session.join();
    if (errors.length) throw new Error(errors.join("; "));
    const guest = session.pc;
    const joinedTransceiver = guest.getTransceivers().find((item) => item.receiver?.track?.kind === "audio");
    await new Promise((resolve) => setTimeout(resolve, 800));
    if (errors.length) throw new Error(errors.join("; "));
    const before = {
      captureCallsAtJoin: captureCalls(),
      sendingTracksAtJoin: guest.getSenders().filter((sender) => sender.track).length,
      outboundAtJoin: await rtp(guest, "outbound-rtp"),
      inboundAtJoin: await rtp(host, "inbound-rtp"),
      directionAtJoin: joinedTransceiver?.currentDirection || "",
      audioSections: (guest.localDescription.sdp.match(/^m=audio /gm) || []).length,
    };
    const remoteSdp = host.remoteDescription.sdp;
    mic = await fakeMicrophone(browserName);
    const attached = await session.attachMicrophone(mic.stream);
    const inboundAfter = await rtp(host, "inbound-rtp");
    let inboundLater = inboundAfter;
    const deadline = Date.now() + 2000;
    while (inboundLater.packets <= before.inboundAtJoin.packets && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      inboundLater = await rtp(host, "inbound-rtp");
    }
    return {
      ...before,
      attached,
      captureCallsAfter: captureCalls(),
      inboundAfter: inboundLater,
      outboundAfter: await rtp(guest, "outbound-rtp"),
      directionAfter: joinedTransceiver?.currentDirection || "",
      remoteSdpUnchanged: host.remoteDescription.sdp === remoteSdp,
      signalingStable: guest.signalingState === "stable",
      trackLabel: mic.label,
      errors: [...errors],
    };
  } finally {
    session?.close();
    host.close();
    mic?.stream?.getTracks().forEach((track) => track.stop());
    if (mic?.context) await mic.context.close();
  }
}
