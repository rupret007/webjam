import { delay } from "./mailbox.js";
import { VideoConflict } from "./video-mailbox.js";
import { cameraDescription, gatherVideo } from "./video-peer.js";
import { connectionRoute } from "./audio.js";

export async function selectedVideoPair(peer) {
  const stats = await peer.getStats();
  const transport = [...stats.values()].find(row => row.type === "transport");
  const id = transport?.selectedCandidatePairId;
  const pair = stats.get(id);
  if (!id || pair?.nominated !== true || pair.state !== "succeeded") return null;
  return { id, route: connectionRoute(stats, id) };
}

function changeServers(peer, servers) {
  peer.setConfiguration({ ...peer.getConfiguration(), iceServers: servers });
}

export class VideoRefresh {
  constructor({ peer, mailbox, credentials, host, signal, onExpired }) {
    Object.assign(this, { peer, mailbox, credentials, host, signal, onExpired });
    this.completed = 0;
    this.latestGeneration = 0;
    this.stopped = false;
  }

  start() {
    this.arm(true);
    this.abortTimer = setInterval(() => {
      if (!this.running && !this.abortRead) void this.observeAbort().catch(() => {});
    }, 1000);
  }

  close() {
    this.stopped = true;
    clearTimeout(this.refreshTimer);
    clearTimeout(this.expiryTimer);
    this.expiryTimer = null;
    clearInterval(this.abortTimer);
  }

  async observeAbort() {
    if (this.stopped || this.signal.aborted) return true;
    this.abortRead ??= this.mailbox.read("video-refresh-abort", "offer").finally(() => {
      this.abortRead = null;
    });
    const value = await this.abortRead;
    if (value?.v === 1 && value.abort === "refresh") this.stopRenewing();
    return this.stopped || this.signal.aborted;
  }

  stopRenewing() {
    this.stopped = true;
    clearTimeout(this.refreshTimer);
    clearInterval(this.abortTimer);
    // Keep the old expiry enforced after a signaling conflict.
  }

  arm(initial = false) {
    if (this.stopped || this.signal.aborted) return;
    clearTimeout(this.refreshTimer);
    clearTimeout(this.expiryTimer);
    const expiry = this.credentials.expiresAt;
    const refreshDelay = expiry - Date.now() - 90000;
    // A short-lived replacement might already be inside the renewal window.
    // Keep its expiry enforced without entering a tight credential request loop.
    this.refreshTimer = initial || refreshDelay > 0
      ? setTimeout(() => void this.run(), Math.max(0, refreshDelay)) : null;
    const expiryTimer = setTimeout(async () => {
      let route;
      try {
        route = (await selectedVideoPair(this.peer))?.route;
      } catch { /* Treat unavailable stats as an unclassified route. */ }
      // A successful renewal or room close can supersede this check while stats are pending.
      if (this.expiryTimer === expiryTimer && !this.signal.aborted && route !== "direct") this.onExpired();
    }, Math.max(0, expiry - Date.now()));
    this.expiryTimer = expiryTimer;
  }

  async latest() {
    // A generation is reserved before an offer is written. The guest only
    // reserves the next index, so the host can check consecutive markers.
    while (this.latestGeneration < 6) {
      const next = this.latestGeneration + 1;
      const mark = await this.mailbox.read(`video-refresh-gen-${next}`, "offer");
      if (mark?.v !== 1 || mark.gen !== next) break;
      this.latestGeneration = next;
    }
    return this.latestGeneration;
  }

  async renew(until, refreshWindow) {
    try { return await this.mailbox.credentials(); }
    catch (error) {
      if (refreshWindow.retried || Date.now() + 15000 >= until) throw error;
      refreshWindow.retried = true;
      await delay(15000, this.signal);
      if (await this.observeAbort() || Date.now() >= until) throw error;
      return this.mailbox.credentials();
    }
  }

  async run() {
    if (this.stopped || this.signal.aborted || this.running) return;
    const until = this.credentials.expiresAt - 30000;
    const refreshWindow = { retried: false };
    this.running = true;
    try {
      if (await this.observeAbort()) return;
      const latest = await this.latest();
      const route = (await selectedVideoPair(this.peer))?.route;
      if (route === "direct" && latest <= this.completed) {
        const priorExpiry = this.credentials.expiresAt;
        const next = await this.renew(until, refreshWindow);
        if (await this.observeAbort()) return;
        changeServers(this.peer, next.iceServers);
        this.credentials = next;
        if (this.host && this.completed < 6) await this.answerUntil(priorExpiry - 15000, next);
        this.arm();
        return;
      }
      if (this.host) {
        if (this.completed < 6) await this.answerUntil(this.credentials.expiresAt - 15000);
      } else {
        while (Date.now() < until) {
          if (await this.observeAbort()) return;
          const n = Math.max(this.completed, await this.latest()) + 1;
          if (n > 6 || this.peer.connectionState !== "connected" || this.peer.signalingState !== "stable") return;
          if (await this.offer(n, until, refreshWindow) || refreshWindow.retried) return;
          refreshWindow.retried = true;
        }
      }
    } catch (error) {
      if (error instanceof VideoConflict && !this.signal.aborted) {
        this.stopRenewing();
        await this.mailbox.abortRefresh().catch(() => {});
      }
    } finally { this.running = false; }
  }

  async offer(n, until, refreshWindow) {
    const round = `video-refresh-${n}`;
    const previous = this.credentials;
    await this.mailbox.write(`video-refresh-gen-${n}`, "offer", { v: 1, gen: n });
    let answerApplied = false;
    let serversChanged = false;
    try {
      const next = await this.renew(until, refreshWindow);
      if (await this.observeAbort() || Date.now() >= until) return false;
      const priorPair = await selectedVideoPair(this.peer);
      changeServers(this.peer, next.iceServers);
      serversChanged = true;
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
          await this.connected(n, next, priorPair);
          return true;
        }
        await delay(Math.min(500, Math.max(0, deadline - Date.now())), this.signal);
      }
      return false;
    } catch (error) {
      if (error instanceof VideoConflict || this.signal.aborted) throw error;
      return false;
    } finally {
      if (serversChanged && !answerApplied && !this.signal.aborted) {
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
        const priorPair = await selectedVideoPair(this.peer);
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
          await this.connected(n, next, priorPair);
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

  async connected(n, next, priorPair) {
    const until = Date.now() + 15000;
    let selected;
    while (Date.now() < until && !this.signal.aborted) {
      selected = await selectedVideoPair(this.peer);
      if (this.peer.connectionState === "connected" && selected && selected.id !== priorPair?.id) {
        this.completed = n;
        this.credentials = next;
        clearTimeout(this.expiryTimer);
        this.arm();
        return;
      }
      await delay(250, this.signal);
    }
    if (this.signal.aborted) return;
    if (this.peer.connectionState === "connected" && selected?.route === "relayed" &&
        selected.id === priorPair?.id && Date.now() < this.credentials.expiresAt) {
      // The previous allocation still works. Keep its original expiry since
      // the new allocation was never observed on the selected pair.
      try {
        changeServers(this.peer, this.credentials.iceServers);
        return;
      } catch { /* Pause video if the old configuration cannot be restored. */ }
    }
    this.onExpired();
  }
}
