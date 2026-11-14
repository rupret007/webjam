import { delay } from "./mailbox.js";
import { VideoConflict } from "./video-mailbox.js";
import { cameraDescription, gatherVideo } from "./video-peer.js";
import { connectionRoute } from "./audio.js";

async function selectedRoute(peer) {
  const stats = await peer.getStats();
  const transport = [...stats.values()].find(row => row.type === "transport");
  const id = transport?.selectedCandidatePairId;
  return stats.get(id)?.nominated === true ? connectionRoute(stats, id) : null;
}

function changeServers(peer, servers) {
  peer.setConfiguration({ ...peer.getConfiguration(), iceServers: servers });
}

export class VideoRefresh {
  constructor({ peer, mailbox, credentials, host, signal, onExpired }) {
    Object.assign(this, { peer, mailbox, credentials, host, signal, onExpired });
    this.completed = 0;
    this.stopped = false;
    this.pollPending = false;
  }

  start() {
    this.arm();
    this.abortTimer = setInterval(() => {
      if (!this.pollPending && !this.running) void this.observeAbort().catch(() => {});
    }, 1000);
  }

  close() {
    this.stopped = true;
    clearTimeout(this.refreshTimer);
    clearTimeout(this.expiryTimer);
    clearInterval(this.abortTimer);
  }

  async observeAbort() {
    if (this.stopped || this.signal.aborted) return true;
    this.pollPending = true;
    try {
      const value = await this.mailbox.read("video-refresh-abort", "offer");
      if (value?.v === 1 && value.abort === "refresh") this.stopRenewing();
      return this.stopped || this.signal.aborted;
    } finally { this.pollPending = false; }
  }

  stopRenewing() {
    this.stopped = true;
    clearTimeout(this.refreshTimer);
    clearInterval(this.abortTimer);
    // Keep the old expiry enforced after a signaling conflict.
  }

  arm() {
    if (this.stopped || this.signal.aborted) return;
    clearTimeout(this.refreshTimer);
    clearTimeout(this.expiryTimer);
    const expiry = this.credentials.expiresAt;
    this.refreshTimer = setTimeout(() => void this.run(), Math.max(0, expiry - Date.now() - 90000));
    this.expiryTimer = setTimeout(async () => {
      try {
        if (await selectedRoute(this.peer) !== "direct" && !this.signal.aborted) this.onExpired();
      } catch { if (!this.signal.aborted) this.onExpired(); }
    }, Math.max(0, expiry - Date.now()));
  }

  async latest() {
    for (let n = 6; n >= 1; n--) {
      const mark = await this.mailbox.read(`video-refresh-gen-${n}`, "offer");
      if (mark?.v === 1 && mark.gen === n) return n;
    }
    return 0;
  }

  async renew(until) {
    try { return await this.mailbox.credentials(); }
    catch (error) {
      if (Date.now() + 15000 >= until) throw error;
      await delay(15000, this.signal);
      if (await this.observeAbort() || Date.now() >= until) throw error;
      return this.mailbox.credentials();
    }
  }

  async run() {
    if (this.stopped || this.signal.aborted) return;
    const until = this.credentials.expiresAt - 30000;
    this.running = true;
    try {
      if (await this.observeAbort()) return;
      const latest = await this.latest();
      const route = await selectedRoute(this.peer);
      if (route === "direct" && latest <= this.completed) {
        const priorExpiry = this.credentials.expiresAt;
        const next = await this.renew(until);
        if (await this.observeAbort()) return;
        changeServers(this.peer, next.iceServers);
        this.credentials = next;
        if (this.host) await this.answerUntil(priorExpiry - 15000, next);
        this.arm();
        return;
      }
      if (this.host) await this.answerUntil(this.credentials.expiresAt - 15000);
      else {
        for (let attempt = 0; attempt < 2 && Date.now() < until; attempt++) {
          if (await this.observeAbort()) return;
          const n = Math.max(this.completed, await this.latest()) + 1;
          if (n > 6 || this.peer.connectionState !== "connected" || this.peer.signalingState !== "stable") return;
          if (await this.offer(n, until)) return;
        }
      }
    } catch (error) {
      if (error instanceof VideoConflict && !this.signal.aborted) {
        this.stopRenewing();
        await this.mailbox.abortRefresh().catch(() => {});
      }
    } finally { this.running = false; }
  }

  async offer(n, until) {
    const round = `video-refresh-${n}`;
    const previous = this.credentials;
    await this.mailbox.write(`video-refresh-gen-${n}`, "offer", { v: 1, gen: n });
    let answerApplied = false;
    try {
      const next = await this.renew(until);
      if (await this.observeAbort() || Date.now() >= until) return false;
      changeServers(this.peer, next.iceServers);
      await this.peer.setLocalDescription(await this.peer.createOffer({ iceRestart: true }));
      const description = await gatherVideo(this.peer, this.signal);
      if (await this.observeAbort()) return false;
      await this.mailbox.write(round, "offer", description);
      const deadline = Date.now() + 15000;
      while (Date.now() < deadline && !await this.observeAbort()) {
        const answer = await this.mailbox.read(round, "answer");
        if (answer && Date.now() < deadline && this.peer.signalingState === "have-local-offer" && !this.stopped) {
          if (await this.observeAbort() || Date.now() >= deadline) return false;
          await this.peer.setRemoteDescription({ type: "answer", sdp: cameraDescription(answer) });
          answerApplied = true;
          await this.connected(n, next);
          return true;
        }
        await delay(Math.min(500, Math.max(0, deadline - Date.now())), this.signal);
      }
      return false;
    } catch (error) {
      if (error instanceof VideoConflict || this.signal.aborted) throw error;
      return false;
    } finally {
      if (!answerApplied && !this.signal.aborted) {
        try {
          if (this.peer.signalingState === "have-local-offer") await this.peer.setLocalDescription({ type: "rollback" });
          changeServers(this.peer, previous.iceServers);
        } catch { this.onExpired(); }
      }
    }
  }

  async answerUntil(until, prefetched) {
    while (Date.now() < until && !await this.observeAbort()) {
      const n = await this.latest();
      const round = `video-refresh-${n}`;
      const offer = n > this.completed ? await this.mailbox.read(round, "offer") : null;
      if (offer && Date.now() < until && this.peer.signalingState === "stable" && n === await this.latest()) {
        const previous = this.peer.getConfiguration().iceServers;
        const next = prefetched ?? await this.mailbox.credentials();
        if (Date.now() >= until || n !== await this.latest()) return;
        changeServers(this.peer, next.iceServers);
        let committed = false;
        try {
          if (await this.observeAbort() || Date.now() >= until) return;
          await this.peer.setRemoteDescription({ type: "offer", sdp: cameraDescription(offer) });
          committed = true;
          await this.peer.setLocalDescription(await this.peer.createAnswer());
          const answer = await gatherVideo(this.peer, this.signal, 10000);
          if (await this.observeAbort()) return;
          await this.mailbox.write(round, "answer", answer);
          await this.connected(n, next);
        } catch (error) {
          if (committed && !(error instanceof VideoConflict) && !this.signal.aborted) this.onExpired();
          throw error;
        } finally {
          if (!committed && !this.signal.aborted) changeServers(this.peer, previous);
        }
        return;
      }
      await delay(Math.min(1000, Math.max(0, until - Date.now())), this.signal);
    }
  }

  async connected(n, next) {
    const until = Date.now() + 15000;
    while (Date.now() < until && !this.signal.aborted) {
      if (this.peer.connectionState === "connected") {
        this.completed = n;
        this.credentials = next;
        clearTimeout(this.expiryTimer);
        this.arm();
        return;
      }
      await delay(250, this.signal);
    }
    if (!this.signal.aborted) this.onExpired();
  }
}
