import { open, roomAddress, seal, turnToken } from "./crypto.js";
import { musicSdp, tuneReceiver, latencyMs } from "./audio.js";

export const BUSY_MESSAGE = "Relay busy, try same Wi-Fi or the app.";
const POLL_MS = 15_000;
const MAX_PARTS = 40;
const CHUNK_SIZE = 1800;

export function delay(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException("Stopped", "AbortError"));
    const finish = () => { signal?.removeEventListener("abort", abort); resolve(); };
    const timer = setTimeout(finish, ms);
    const abort = () => { clearTimeout(timer); reject(new DOMException("Stopped", "AbortError")); };
    signal?.addEventListener("abort", abort, { once: true });
  });
}

export class RelayError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

export class MailboxTransport {
  constructor(origin, invite, { signal, fetchImpl = globalThis.fetch, interval = 2500, onStatus = () => {} } = {}) {
    const url = new URL(origin);
    if ((url.protocol !== "https:" && !(url.protocol === "http:" && ["localhost", "127.0.0.1"].includes(url.hostname))) || url.origin !== origin) throw new Error("Invalid relay origin");
    this.origin = origin;
    this.invite = invite;
    this.signal = signal;
    this.fetchImpl = fetchImpl.bind(globalThis);
    this.interval = interval;
    this.onStatus = onStatus;
    this.tail = Promise.resolve();
    this.nextAt = 0;
    this.addresses = new Map();
  }

  address(address, slot) {
    const name = `${address}/${slot}`;
    if (!this.addresses.has(name)) this.addresses.set(name, roomAddress(this.invite.capability, this.invite.sessionId, address, slot));
    return this.addresses.get(name);
  }

  request(path, options = {}) {
    const run = async () => {
      await delay(Math.max(0, this.nextAt - Date.now()), this.signal);
      for (let attempt = 0; attempt < 2; attempt++) {
        if (Date.now() >= this.invite.created + 600_000) throw new RelayError("The invite join window has ended. Create a new Sketch Jam link.", 410);
        this.nextAt = Date.now() + this.interval;
        const requestController = new AbortController();
        const abort = () => requestController.abort();
        this.signal?.addEventListener("abort", abort, { once: true });
        if (this.signal?.aborted) abort();
        const timer = setTimeout(() => requestController.abort(new DOMException("The relay request timed out. Try again with a new link.", "TimeoutError")), 20_000);
        let response;
        let body = null;
        try {
          response = await this.fetchImpl(`${this.origin}${path}`, { ...options, signal: requestController.signal, credentials: "omit", cache: "no-store", referrerPolicy: "no-referrer" });
          if (![204, 410].includes(response.status)) {
            try { body = await response.json(); } catch { throw new RelayError([429, 503].includes(response.status) ? BUSY_MESSAGE : "The relay returned an invalid response.", response.status); }
          }
        } finally {
          clearTimeout(timer);
          this.signal?.removeEventListener("abort", abort);
        }
        if (response.status === 429 && body?.error === "rate" && attempt === 0) {
          this.onStatus("Signaling is busy on this network. Waiting one minute before retrying…");
          await delay(65_000, this.signal);
          continue;
        }
        if ([429, 503].includes(response.status)) throw new RelayError(BUSY_MESSAGE, response.status);
        return { status: response.status, body };
      }
      throw new RelayError(BUSY_MESSAGE, 429);
    };
    const promise = this.tail.then(run);
    this.tail = promise.catch(() => {});
    return promise;
  }

  async put(address, slot, value) {
    const material = await this.address(address, slot);
    const payload = await seal(material.key, JSON.stringify(value), material.aad);
    const result = await this.request(`/mailbox/${material.id}/${slot}?client=web`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ payload }) });
    if (result.status === 409) return false;
    if (result.status !== 201) throw new RelayError("Unable to send encrypted signaling.", result.status);
    return true;
  }

  async get(address, slot) {
    const material = await this.address(address, slot);
    const result = await this.request(`/mailbox/${material.id}/${slot}?client=web`);
    if (result.status === 204) return null;
    if (result.status !== 200 || typeof result.body?.payload !== "string") throw new RelayError("This Sketch Jam invite has expired or is unavailable.", result.status);
    return JSON.parse(await open(material.key, result.body.payload, material.aad));
  }

  async wait(address, slot) {
    while (!this.signal?.aborted) {
      const value = await this.get(address, slot);
      if (value !== null) return value;
      await delay(POLL_MS, this.signal);
    }
    throw new DOMException("Stopped", "AbortError");
  }

  async sendDescription(address, kind, description) {
    const text = JSON.stringify({ type: description.type, sdp: description.sdp });
    const total = Math.ceil(text.length / CHUNK_SIZE);
    if (total < 1 || total > MAX_PARTS) throw new Error("Audio signaling is too large.");
    // Publish the first part last. Its existence means every part is available.
    for (const part of [...Array.from({ length: total - 1 }, (_, index) => index + 1), 0]) {
      const value = { part, total, text: text.slice(part * CHUNK_SIZE, (part + 1) * CHUNK_SIZE) };
      if (!await this.put(`${address}/${kind}/${part}`, kind, value)) throw new Error("This peer slot has already been used. Create a new invite.");
    }
  }

  async receiveDescription(address, kind) {
    const first = await this.wait(`${address}/${kind}/0`, kind);
    if (first?.part !== 0 || !Number.isInteger(first.total) || first.total < 1 || first.total > MAX_PARTS || typeof first.text !== "string" || first.text.length > CHUNK_SIZE) throw new Error("Invalid encrypted signaling.");
    let text = first.text;
    for (let part = 1; part < first.total; part++) {
      const next = await this.get(`${address}/${kind}/${part}`, kind);
      if (next?.part !== part || next.total !== first.total || typeof next.text !== "string" || next.text.length > CHUNK_SIZE) throw new Error("Invalid encrypted signaling.");
      text += next.text;
    }
    const description = JSON.parse(text);
    if (description?.type !== kind || typeof description.sdp !== "string" || description.sdp.length > 65_536) throw new Error("Invalid encrypted audio description.");
    return description;
  }

  async turnCredentials() {
    const result = await this.request("/turn-credentials", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ session_id: this.invite.sessionId, invite_token: await turnToken(this.invite.capability, this.invite.sessionId), client: "web" }),
    });
    if (result.status !== 200 || !Array.isArray(result.body?.iceServers) || result.body.iceServers.length === 0 || !Number.isFinite(result.body.ttl) || result.body.ttl <= 0 || result.body.ttl > 3600 || !Number.isFinite(result.body.quota_bytes) || result.body.quota_bytes <= 0 || result.body.quota_bytes > 256 * 1024 * 1024) throw new RelayError(BUSY_MESSAGE, result.status);
    return { iceServers: result.body.iceServers, expiresAt: Date.now() + result.body.ttl * 1000, quotaBytes: result.body.quota_bytes };
  }
}

async function gatherIce(pc, signal) {
  if (pc.iceGatheringState === "complete") return;
  await new Promise((resolve, reject) => {
    const done = () => { cleanup(); resolve(); };
    const check = () => { if (pc.iceGatheringState === "complete") done(); };
    const abort = () => { cleanup(); reject(new DOMException("Stopped", "AbortError")); };
    const timer = setTimeout(done, 7000);
    const cleanup = () => { clearTimeout(timer); pc.removeEventListener("icegatheringstatechange", check); signal.removeEventListener("abort", abort); };
    pc.addEventListener("icegatheringstatechange", check);
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
  });
}

async function connected(pc, signal, timeout) {
  if (pc.connectionState === "connected") return true;
  return new Promise((resolve, reject) => {
    const done = (value) => { cleanup(); resolve(value); };
    const check = () => { if (pc.connectionState === "connected") done(true); else if (["failed", "closed"].includes(pc.connectionState)) done(false); };
    const abort = () => { cleanup(); reject(new DOMException("Stopped", "AbortError")); };
    const timer = setTimeout(() => done(false), timeout);
    const cleanup = () => { clearTimeout(timer); pc.removeEventListener("connectionstatechange", check); signal.removeEventListener("abort", abort); };
    pc.addEventListener("connectionstatechange", check);
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
  });
}

export class SketchRoom {
  constructor({ origin, invite, stream, audioContext, onStatus, onPeer, onTrack, onError }) {
    this.invite = invite;
    this.stream = stream;
    this.audioContext = audioContext;
    this.onStatus = onStatus;
    this.onPeer = onPeer;
    this.onTrack = onTrack;
    this.onError = onError;
    this.controller = new AbortController();
    this.transport = new MailboxTransport(origin, invite, { signal: this.controller.signal, onStatus });
    this.connections = new Map();
    this.participants = new Map();
    this.turn = null;
    this.relayPeers = new Set();
    this.relayBytes = new Map();
    this.relayLimit = 256 * 1024 * 1024;
  }

  async join(name) {
    this.memberId = crypto.randomUUID();
    for (this.index = 0; this.index < 4; this.index++) {
      if (await this.transport.put(`member/${this.index}`, "offer", { id: this.memberId, name: name.trim().slice(0, 24) || "Musician" })) break;
    }
    if (this.index >= 4) throw new Error("This Sketch Jam is full (4 people). Create a new link.");
    this.onStatus("Joined. Share the invite link with up to 3 people. New joins close after 10 minutes.");
    for (let index = 0; index < this.index; index++) {
      const member = await this.transport.get(`member/${index}`, "offer");
      this.addParticipant(index, member);
    }
    this.watchMembers().catch((error) => {
      if (error.status === 410) this.onStatus("The join window has ended. Connected audio can continue.");
      else this.fail(error);
    });
    this.statsTimer = setInterval(() => this.updateStats(), 3000);
  }

  async watchMembers() {
    for (let index = this.index + 1; index < 4; index++) {
      while (!this.controller.signal.aborted && Date.now() < this.invite.created + 570_000) {
        const member = await this.transport.get(`member/${index}`, "offer");
        if (member) { this.addParticipant(index, member); break; }
        await delay(POLL_MS, this.controller.signal);
      }
    }
  }

  addParticipant(index, member) {
    if (!member || typeof member.id !== "string" || !/^[0-9a-f-]{36}$/.test(member.id) || typeof member.name !== "string" || member.name.length > 24) throw new Error("Invalid encrypted room membership.");
    if (this.participants.has(index)) return;
    this.participants.set(index, member);
    this.onPeer({ index, name: member.name, state: "connecting" });
    this.connectPeer(index, member).catch((error) => {
      if (error.status === 410) {
        this.connections.get(index)?.close();
        this.onPeer({ index, name: member.name, state: "failed" });
        this.onStatus("The join window has ended. Connected audio can continue.");
      } else this.fail(error);
    });
  }

  async connectPeer(index, member) {
    const initiating = this.index > index;
    const pair = [this.index, index].sort((a, b) => a - b).join("-");
    for (const round of ["direct", "relay"]) {
      let iceServers = [{ urls: "stun:stun.cloudflare.com:3478" }];
      if (round === "relay") {
        this.onStatus("Direct connection could not reach this peer. Trying the free relay…");
        this.turn ??= this.transport.turnCredentials();
        let credentials = await this.turn;
        if (credentials.expiresAt <= Date.now() + 15_000) {
          this.turn = this.transport.turnCredentials();
          credentials = await this.turn;
        }
        this.relayLimit = Math.min(this.relayLimit, credentials.quotaBytes);
        iceServers = credentials.iceServers;
        this.relayPeers.add(index);
      }
      if (this.controller.signal.aborted) return;
      const pc = new RTCPeerConnection({ iceServers, bundlePolicy: "max-bundle" });
      this.connections.set(index, pc);
      pc.addTrack(this.stream.getAudioTracks()[0], this.stream);
      pc.addEventListener("track", (event) => {
        tuneReceiver(event.receiver);
        this.onTrack(index, event.streams[0] || new MediaStream([event.track]));
      });
      pc.addEventListener("connectionstatechange", () => {
        if (this.connections.get(index) === pc) this.onPeer({ index, name: member.name, state: pc.connectionState });
      });
      const address = `pair/${pair}/${round}`;
      if (initiating) {
        const offer = await pc.createOffer();
        await pc.setLocalDescription({ type: "offer", sdp: musicSdp(offer.sdp) });
        await gatherIce(pc, this.controller.signal);
        await this.transport.sendDescription(address, "offer", pc.localDescription);
        await pc.setRemoteDescription(await this.transport.receiveDescription(address, "answer"));
        if (!await this.transport.put(`${address}/ready`, "offer", { ready: true })) throw new Error("This peer connection has already been used.");
      } else {
        await pc.setRemoteDescription(await this.transport.receiveDescription(address, "offer"));
        const answer = await pc.createAnswer();
        await pc.setLocalDescription({ type: "answer", sdp: musicSdp(answer.sdp) });
        await gatherIce(pc, this.controller.signal);
        await this.transport.sendDescription(address, "answer", pc.localDescription);
        const ready = await this.transport.wait(`${address}/ready`, "offer");
        if (ready?.ready !== true) throw new Error("Invalid encrypted connection acknowledgement.");
      }
      for (const receiver of pc.getReceivers()) tuneReceiver(receiver);
      if (await connected(pc, this.controller.signal, initiating ? 12_000 : 25_000)) {
        this.onStatus("Connected. Headphones on; enjoy your Sketch Jam.");
        return;
      }
      pc.close();
    }
    this.onPeer({ index, name: member.name, state: "failed" });
    this.onStatus("Could not connect to this peer. Try the same Wi-Fi or the WebJam app.");
  }

  async updateStats() {
    if (this.statsPending || this.controller.signal.aborted) return;
    this.statsPending = true;
    try {
      for (const [index, pc] of this.connections) {
        if (pc.connectionState !== "connected") continue;
        try {
          let timer;
          const timeout = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("Audio statistics unavailable")), 3000); });
          let stats;
          try { stats = await Promise.race([pc.getStats(), timeout]); }
          finally { clearTimeout(timer); }
          const latency = latencyMs(stats, this.audioContext.baseLatency);
          if (this.relayPeers.has(index)) {
            let bytes = 0;
            let transports = 0;
            for (const report of stats.values()) if (report.type === "transport") {
              if (!Number.isFinite(report.bytesSent) || report.bytesSent < 0 || !Number.isFinite(report.bytesReceived) || report.bytesReceived < 0) throw new Error("Audio statistics unavailable");
              bytes += report.bytesSent + report.bytesReceived;
              transports++;
            }
            if (!transports || !Number.isFinite(bytes)) throw new Error("Audio statistics unavailable");
            this.relayBytes.set(index, bytes);
            // Stop below the credential's session allowance, reserving in-flight audio.
            if ([...this.relayBytes.values()].reduce((sum, value) => sum + value, 0) >= this.relayLimit * 0.9) {
              this.fail(new RelayError(BUSY_MESSAGE, 429));
              return;
            }
          }
          if (!this.controller.signal.aborted) this.onPeer({ index, name: this.participants.get(index).name, state: pc.connectionState, latency });
        } catch {
          if (this.relayPeers.has(index) && !this.controller.signal.aborted) {
            this.fail(new RelayError(BUSY_MESSAGE, 503));
            return;
          }
          // Direct peers can show unavailable latency when their browser omits stats.
        }
      }
    } finally { this.statsPending = false; }
  }

  fail(error) {
    if (this.controller.signal.aborted || error.name === "AbortError") return;
    this.close();
    this.onError(error);
  }

  close() {
    this.controller.abort();
    clearInterval(this.statsTimer);
    for (const pc of this.connections.values()) pc.close();
    this.connections.clear();
    this.stream.getTracks().forEach((track) => track.stop());
    this.turn = null;
    this.transport.addresses.clear();
  }
}
