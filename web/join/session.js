import { open, seal, validateOffer, sdpFingerprint, enrollmentProof } from "./crypto.js";
import { Mailbox, BUSY_MESSAGE } from "./mailbox.js";
import { policySdp, SESSION_BYTES } from "./policy.js";
import { musicSdp, tuneReceiver, latencyMs } from "./audio.js";

export function waitFor(target, event, check, signal, timeout, message) {
  return new Promise((resolve, reject) => {
    const cleanup = () => { clearTimeout(timer); target.removeEventListener(event, changed); signal.removeEventListener("abort", abort); };
    const changed = () => { if (check()) { cleanup(); resolve(); } };
    const abort = () => { cleanup(); reject(new DOMException("Stopped", "AbortError")); };
    const timer = setTimeout(() => { cleanup(); reject(new Error(message)); }, timeout);
    target.addEventListener(event, changed); signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort(); else changed();
  });
}
export class WebJoinSession {
  constructor({ origin, invite, stream, audioContext, onStatus = () => {}, onTrack = () => {}, onLatency = () => {}, onError = () => {} }) {
    Object.assign(this, { invite, stream, audioContext, onStatus, onTrack, onLatency, onError });
    this.controller = new AbortController();
    this.mailbox = new Mailbox(origin, invite, { signal: this.controller.signal });
    this.authorized = false; this.quotaBytes = SESSION_BYTES; this.previousBytes = 0;
    this.stream.getAudioTracks().forEach((track) => { track.enabled = false; });
  }
  async join() {
    const signal = this.controller.signal;
    this.onStatus("Checking the host invitation…");
    const offer = validateOffer(this.invite, await open(this.invite, await this.mailbox.receive("offer")));
    const remoteSDP = policySdp(offer.sdp, { remote: true });
    const credentials = await this.mailbox.credentials();
    this.quotaBytes = Math.min(this.quotaBytes, credentials.quotaBytes);
    const certificate = await RTCPeerConnection.generateCertificate({ name: "ECDSA", namedCurve: "P-256" });
    if (signal.aborted) throw new DOMException("Stopped", "AbortError");
    const pc = this.pc = new RTCPeerConnection({ certificates: [certificate], iceServers: credentials.iceServers, iceTransportPolicy: "all", bundlePolicy: "max-bundle" });
    // ICE prioritizes direct srflx pairs; already-gathered TURN UDP/TCP/TLS
    // candidates let restrictive networks fall back within the same deadline.
    this.onStatus("Connecting directly, with free relay fallback…");
    pc.addEventListener("track", (event) => {
      tuneReceiver(event.receiver);
      const stream = event.streams[0] || new MediaStream([event.track]);
      if (this.authorized) this.onTrack(stream); else this.pendingStream = stream;
    });
    pc.addEventListener("connectionstatechange", () => {
      if (["failed", "closed"].includes(pc.connectionState) && !signal.aborted) this.fail(new Error("Connection ended. Ask the host for a new invite."));
      if (pc.connectionState === "disconnected") this.disconnectTimer ??= setTimeout(() => this.fail(new Error("The network connection timed out.")), 10000);
      if (pc.connectionState === "connected") { clearTimeout(this.disconnectTimer); this.disconnectTimer = null; }
    });
    let answer;
    let channelSeen = false;
    const enrolled = new Promise((resolve, reject) => {
      this.enrollmentReject = reject;
      pc.addEventListener("datachannel", (event) => {
        const channel = event.channel;
        if (channelSeen || channel.label !== "webjoin-enrollment" || !channel.ordered || channel.maxRetransmits !== null || channel.maxPacketLifeTime !== null) { channel.close(); reject(new Error("Unexpected enrollment channel")); return; }
        channelSeen = true; this.channel = channel;
        let sent = false;
        const prove = async () => {
          if (sent || !answer || channel.readyState !== "open") return;
          sent = true;
          try { channel.send(await enrollmentProof(this.invite, answer)); } catch (error) { reject(error); }
        };
        channel.addEventListener("open", prove);
        channel.addEventListener("message", (message) => {
          if (!sent || message.data !== "authorized" || this.authorized) { reject(new Error("Invalid enrollment response")); this.fail(new Error("Invalid enrollment response")); return; }
          this.authorized = true; resolve();
        });
        channel.addEventListener("close", () => { if (!signal.aborted) this.fail(new Error("The host ended this web session.")); });
        prove();
      });
    });
    // Observe early channel errors while gathering/sending the answer.
    enrolled.catch(() => {});
    await pc.setRemoteDescription({ type: "offer", sdp: remoteSDP });
    pc.addTrack(this.stream.getAudioTracks()[0], this.stream);
    const description = await pc.createAnswer();
    await pc.setLocalDescription({ type: "answer", sdp: musicSdp(description.sdp) });
    await waitFor(pc, "icegatheringstatechange", () => pc.iceGatheringState === "complete", signal, 15000, "Network discovery timed out. Try another network or the app.");
    const sdp = policySdp(pc.localDescription.sdp);
    answer = { ...offer, kind: "answer", guest_pin: sdpFingerprint(sdp), sdp };
    await this.mailbox.send("answer", await seal(this.invite, answer));
    let timer;
    const stopped = new Promise((_, reject) => { signal.addEventListener("abort", () => reject(new DOMException("Stopped", "AbortError")), { once: true }); });
    try {
      await Promise.race([enrolled, stopped, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("Host authentication timed out. Ask for a new invite.")), 30000); })]);
    } finally { clearTimeout(timer); }
    if (signal.aborted) throw new DOMException("Stopped", "AbortError");
    this.stream.getAudioTracks().forEach((track) => { track.enabled = true; });
    if (this.pendingStream) this.onTrack(this.pendingStream);
    this.pendingStream = null;
    this.onStatus("Connected — Web Join, higher delay (about 60–120 ms).");
    this.statsTimer = setInterval(() => this.updateStats(), 1000);
    // Credentials and their allocation authority are never retained past TTL.
    this.expiryTimer = setTimeout(() => this.fail(new Error("The free relay session expired. Ask the host for a new invite.")), Math.max(0, credentials.expiresAt - Date.now()));
  }
  async updateStats() {
    if (this.statsPending || this.controller.signal.aborted || !this.authorized) return;
    this.statsPending = true;
    let timer;
    try {
      const stats = await Promise.race([this.pc.getStats(), new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(BUSY_MESSAGE)), 3000); })]);
      let bytes = 0, transports = 0;
      for (const report of stats.values()) if (report.type === "transport") {
        if (!Number.isSafeInteger(report.bytesSent) || report.bytesSent < 0 || !Number.isSafeInteger(report.bytesReceived) || report.bytesReceived < 0) throw new Error(BUSY_MESSAGE);
        bytes += report.bytesSent + report.bytesReceived; transports++;
      }
      if (!transports || bytes < this.previousBytes || bytes >= this.quotaBytes * 0.9) throw new Error(BUSY_MESSAGE);
      this.previousBytes = bytes;
      this.onLatency(latencyMs(stats, this.audioContext.baseLatency));
    } catch (error) { this.fail(error); }
    finally { clearTimeout(timer); this.statsPending = false; }
  }
  fail(error) { if (this.controller.signal.aborted) return; this.close(); this.onError(error); }
  close() {
    if (this.controller.signal.aborted) return;
    this.controller.abort();
    clearInterval(this.statsTimer); clearTimeout(this.expiryTimer); clearTimeout(this.disconnectTimer);
    this.channel?.close(); this.pc?.close();
    this.stream.getTracks().forEach((track) => track.stop());
    this.pendingStream = null; this.authorized = false;
  }
}
