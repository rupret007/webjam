import { open, seal, validateOffer, sdpFingerprint, enrollmentProof } from "./crypto.js";
import { Mailbox, BUSY_MESSAGE } from "./mailbox.js";
import { candidateAllowed, offerHasReviewedAllocation, policySdp, SESSION_BYTES } from "./policy.js";
import { musicSdp, tuneReceiver, latencyMs, adaptJitterBufferTarget, applyAdaptiveReceiverTarget, connectionRoute } from "./audio.js";
import { JOIN_SIGNAL } from "./errors.js";
import { listeningLine } from "./listening.js";

const SEND_DIRECTIONS = new Set(["sendrecv", "sendonly"]);

function waitForAbort(ms, signal) {
  return new Promise((resolve) => {
    const finish = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", finish);
      resolve();
    };
    const timer = setTimeout(finish, ms);
    signal?.addEventListener("abort", finish);
    if (signal?.aborted) finish();
  });
}

async function outboundAudioFlowing(pc, sender, signal) {
  const deadline = Date.now() + 5000;
  let seen = null;
  while (Date.now() < deadline) {
    if (signal.aborted) return false;
    let packets = 0;
    let bytes = 0;
    let stats;
    try { stats = await sender.getStats(); } catch { return false; }
    for (const report of stats.values()) {
      if (report.type !== "outbound-rtp" || (report.kind !== "audio" && report.mediaType !== "audio")) continue;
      packets += Number(report.packetsSent) || 0;
      bytes += Number(report.bytesSent) || 0;
    }
    const transceiver = pc.getTransceivers().find((item) => item.sender === sender);
    if (seen && SEND_DIRECTIONS.has(transceiver?.currentDirection) && packets > seen.packets && bytes > seen.bytes) return true;
    seen = { packets, bytes };
    await waitForAbort(100, signal);
  }
  return false;
}

export async function gatherAnswerSDP(pc, signal, { timeout = 15000, allowReviewedAllocations = false, allowHostCandidates = false } = {}) {
  await new Promise((resolve, reject) => {
    let graceElapsed = false;
    const cleanup = () => {
      clearTimeout(timer);
      clearTimeout(grace);
      pc.removeEventListener("icegatheringstatechange", changed);
      pc.removeEventListener("icecandidate", changed);
      signal.removeEventListener("abort", abort);
    };
    const finish = () => { cleanup(); resolve(); };
    const changed = () => {
      if (pc.iceGatheringState === "complete" || (graceElapsed &&
          pc.localDescription?.sdp.split(/\r?\n/).some(line => line.startsWith("a=candidate:") &&
            candidateAllowed(line, undefined, allowReviewedAllocations, allowHostCandidates)))) finish();
    };
    const abort = () => { cleanup(); reject(new DOMException("Stopped", "AbortError")); };
    // Give alternate services time to gather, then signal vetted candidates
    // while ICE can still use them. Keep waiting if we have no usable path.
    const grace = setTimeout(() => { graceElapsed = true; changed(); }, Math.min(3500, timeout));
    const timer = setTimeout(finish, timeout);
    pc.addEventListener("icegatheringstatechange", changed);
    pc.addEventListener("icecandidate", changed);
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort(); else changed();
  });
  if (signal.aborted) throw new DOMException("Stopped", "AbortError");
  return policySdp(pc.localDescription?.sdp ?? "", { allowReviewedAllocations, allowHostCandidates });
}
export class WebJoinSession {
  constructor({ origin, invite, stream, audioContext, onStatus = () => {}, onTrack = () => {}, onLatency = () => {}, onRoute = () => {}, onError = () => {}, loopbackLab = false, sessionName = "" }) {
    Object.assign(this, { invite, stream: stream || null, audioContext, onStatus, onTrack, onLatency, onRoute, onError, sessionName });
    // Tests only. Production join leaves this false and still strips host candidates.
    this.loopbackLab = loopbackLab === true;
    this.listenOnly = !this.stream;
    this.controller = new AbortController();
    this.mailbox = new Mailbox(origin, invite, { signal: this.controller.signal });
    this.authorized = false; this.quotaBytes = SESSION_BYTES; this.previousBytes = 0;
    this.accessExpiresAt = null;
    this.route = null;
    this._jitterAdaptation = { targetMs: 20, lastChangeAt: 0 };
    this.stream?.getAudioTracks().forEach((track) => { track.enabled = false; });
  }
  async attachMicrophone(stream) {
    const track = stream?.getAudioTracks?.()[0];
    const pc = this.pc;
    const signal = this.controller.signal;
    if (!track || track.readyState !== "live" || !pc || signal.aborted) return false;
    const transceiver = pc.getTransceivers?.().find((item) => item.receiver?.track?.kind === "audio" && item.mid != null)
      ?? pc.getTransceivers?.().find((item) => item.receiver?.track?.kind === "audio");
    const sender = transceiver?.sender;
    if (!sender) return false;
    const previousDirection = transceiver.direction;
    const previousTrack = sender.track ?? null;
    // Direction changes after the sealed answer do not send. currentDirection
    // stays recvonly until a renegotiation this host does not accept.
    if (previousDirection === "recvonly" || previousDirection === "inactive") transceiver.direction = "sendrecv";
    let flowing = false;
    try {
      await sender.replaceTrack(track);
      track.enabled = true;
      flowing = !signal.aborted && await outboundAudioFlowing(pc, sender, signal);
    } catch { flowing = false; }
    if (!flowing) {
      try { transceiver.direction = previousDirection; } catch { /* The peer may already be closed. */ }
      await sender.replaceTrack(previousTrack).catch(() => {});
      return false;
    }
    this.stream = stream;
    this.listenOnly = false;
    return true;
  }
  async join() {
    const signal = this.controller.signal;
    this.onStatus("Checking the host invitation…");
    const offer = validateOffer(this.invite, await open(this.invite, await this.mailbox.receive("offer")));
    const remoteSDP = policySdp(offer.sdp, { remote: true, allowHostCandidates: this.loopbackLab });
    const credentials = this.loopbackLab
      ? { iceServers: [], expiresAt: this.invite.expiresAt * 1000, quotaBytes: this.quotaBytes }
      : await this.mailbox.credentials();
    this.accessExpiresAt = credentials.expiresAt;
    this.quotaBytes = Math.min(this.quotaBytes, credentials.quotaBytes);
    const certificate = await RTCPeerConnection.generateCertificate({ name: "ECDSA", namedCurve: "P-256" });
    if (signal.aborted) throw new DOMException("Stopped", "AbortError");
    const pc = this.pc = new RTCPeerConnection({ certificates: [certificate], iceServers: credentials.iceServers, iceTransportPolicy: "all", bundlePolicy: "max-bundle" });
    // ICE prioritizes direct srflx pairs; already-gathered TURN UDP/TCP/TLS
    // candidates let restrictive networks fall back within the same deadline.
    this.onStatus("Connecting directly, with free relay fallback…");
    pc.addEventListener("track", (event) => {
      tuneReceiver(event.receiver, this._jitterAdaptation.targetMs);
      this._inboundReceiver = event.receiver;
      const stream = event.streams[0] || new MediaStream([event.track]);
      if (this.authorized) this.onTrack(stream); else this.pendingStream = stream;
    });
    pc.addEventListener("connectionstatechange", () => {
      if (["failed", "closed"].includes(pc.connectionState) && !signal.aborted) {
        this.fail(new Error(this.authorized ? JOIN_SIGNAL.SESSION_ENDED : JOIN_SIGNAL.ICE_CONNECTION_FAILED));
      }
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
        channel.addEventListener("close", () => {
          if (!signal.aborted) this.fail(new Error(this.authorized ? JOIN_SIGNAL.SESSION_ENDED : JOIN_SIGNAL.ICE_CONNECTION_FAILED));
        });
        prove();
      });
    });
    // Observe early channel errors while gathering/sending the answer.
    enrolled.catch(() => {});
    await pc.setRemoteDescription({ type: "offer", sdp: remoteSDP });
    if (this.stream?.getAudioTracks().length) pc.addTrack(this.stream.getAudioTracks()[0], this.stream);
    else {
      const audio = pc.getTransceivers().find((item) => item.receiver?.track?.kind === "audio");
      if (!audio) throw new Error("The host offer did not include audio.");
      // The host keeps a single sealed answer and will not renegotiate. A
      // recvonly answer leaves currentDirection recvonly, so replaceTrack
      // never sends. Negotiate sendrecv with no track; RTP starts only when
      // a microphone is attached.
      audio.direction = "sendrecv";
    }
    const description = await pc.createAnswer();
    await pc.setLocalDescription({ type: "answer", sdp: musicSdp(description.sdp) });
    // An older host rejects these addresses in an answer. Its authenticated
    // offer advertises only the legacy candidates, so keep that answer policy.
    const sdp = await gatherAnswerSDP(pc, signal, { allowReviewedAllocations: offerHasReviewedAllocation(remoteSDP), allowHostCandidates: this.loopbackLab });
    answer = { ...offer, kind: "answer", guest_pin: sdpFingerprint(sdp), sdp };
    await this.mailbox.send("answer", await seal(this.invite, answer));
    let timer;
    const stopped = new Promise((_, reject) => { signal.addEventListener("abort", () => reject(new DOMException("Stopped", "AbortError")), { once: true }); });
    try {
      await Promise.race([enrolled, stopped, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("Host authentication timed out. Ask for a new invite.")), 30000); })]);
    } finally { clearTimeout(timer); }
    if (signal.aborted) throw new DOMException("Stopped", "AbortError");
    this.stream?.getAudioTracks().forEach((track) => { track.enabled = true; });
    if (this.pendingStream) this.onTrack(this.pendingStream);
    this.pendingStream = null;
    this.onStatus(listeningLine(this.sessionName));
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
      if (this.controller.signal.aborted || !this.authorized) return;
      let bytes = 0, transports = 0, selectedPairId;
      for (const report of stats.values()) if (report.type === "transport") {
        if (!Number.isSafeInteger(report.bytesSent) || report.bytesSent < 0 || !Number.isSafeInteger(report.bytesReceived) || report.bytesReceived < 0) throw new Error(BUSY_MESSAGE);
        bytes += report.bytesSent + report.bytesReceived; transports++;
        selectedPairId = report.selectedCandidatePairId;
      }
      if (!transports || bytes < this.previousBytes || bytes >= this.quotaBytes * 0.9) throw new Error(BUSY_MESSAGE);
      this.previousBytes = bytes;
      this._jitterAdaptation = adaptJitterBufferTarget(stats, this._jitterAdaptation);
      if (this._inboundReceiver) {
        applyAdaptiveReceiverTarget(this._inboundReceiver, this._jitterAdaptation);
      }
      this.onLatency(latencyMs(stats, this.audioContext.baseLatency));
      // Web Join bundles audio and enrollment on one transport. With missing
      // or ambiguous stats, leave the route unknown.
      const route = transports === 1 && this.pc.connectionState === "connected"
        ? connectionRoute(stats, selectedPairId) : null;
      if (route !== this.route) {
        this.route = route;
        this.onRoute(route);
      }
    } catch (error) { this.fail(error); }
    finally { clearTimeout(timer); this.statsPending = false; }
  }
  fail(error) { if (this.controller.signal.aborted) return; this.close(); this.onError(error); }
  close() {
    if (this.controller.signal.aborted) return;
    this.accessExpiresAt = null;
    this.controller.abort();
    clearInterval(this.statsTimer); clearTimeout(this.expiryTimer); clearTimeout(this.disconnectTimer);
    this.channel?.close(); this.pc?.close();
    this.stream?.getTracks().forEach((track) => track.stop());
    this.pendingStream = null; this._inboundReceiver = null; this.authorized = false;
  }
}
