import { VideoMailbox } from "./video-mailbox.js";
import { cameraDescription, gatherVideo, preferVp8 } from "./video-peer.js";
import { videoRelayBudget } from "./video-guard.js";
import { VideoRefresh } from "./video-refresh.js";

const DISABLED = "In-room camera is off until video relay is configured. Music stays connected.";
const DROPPED = "Video paused. The camera connection dropped.";
// Each camera call uses its own signaling rounds. Bound the restarts so a
// flapping network cannot spend the daily signaling allowance.
export const MAX_CAMERA_CALLS = 4;
const cameraSizes = { width: { ideal: 640 }, height: { ideal: 360 }, frameRate: { ideal: 15, max: 15 } };
const cameraErrors = {
  NotAllowedError: "Camera access was blocked. Use the site controls beside the address bar to allow the camera, then tap Try again.",
  SecurityError: "Camera access was blocked. Use the site controls beside the address bar to allow the camera, then tap Try again.",
  NotFoundError: "No camera found. Connect a camera, then tap Try again.",
  OverconstrainedError: "No camera found. Connect a camera, then tap Try again.",
  NotReadableError: "Couldn't open the camera. Close the other app using it, then tap Try again.",
};

export class VideoRoom {
  constructor({ root, origin, invite, host, timingReady = () => true }) {
    Object.assign(this, { root, origin, invite, host, timingReady });
    this.label = host ? "Show my camera" : "Show camera";
    this.call = 1;
    this.relayBudget = { counted: 0, watermark: 0 };
    this.startCall();
    this.camera = root.querySelector("[data-camera]");
    this.switcher = root.querySelector("[data-camera-switch]");
    this.preview = root.querySelector("[data-self]");
    this.remote = root.querySelector("[data-remote]");
    this.placeholder = root.querySelector(".video-placeholder");
    this.status = root.querySelector("[data-video-status]");
    this.buttonClick = () => void (this.state === "relay-off" ? this.checkRelay() : this.tap());
    this.switchClick = () => void this.tap(true);
    this.devicesChanged = () => {
      if (this.track) void this.refreshCameraDevices();
    };
    this.visible = () => {
      if (!document.hidden && this.state === "relay-off" && Date.now() - (this.lastCheck ?? 0) >= 30000) void this.checkRelay();
    };
    this.camera.addEventListener("click", this.buttonClick);
    this.switcher.addEventListener("click", this.switchClick);
    document.addEventListener("visibilitychange", this.visible);
    navigator.mediaDevices?.addEventListener?.("devicechange", this.devicesChanged);
    root.hidden = false;
    this.render("checking", "Checking camera availability…");
    this.expiryTimer = setTimeout(() => this.pause("Video paused. Ask your host for a fresh invite."), Math.max(0, invite.expiresAt * 1000 - Date.now()));
    void this.checkRelay();
  }

  // Per-call signaling. Leave and final pauses abort it for good; a
  // resumable pause replaces it with the next call's.
  startCall() {
    this.controller = new AbortController();
    this.signal = this.controller.signal;
    this.mailbox = new VideoMailbox(this.origin, this.invite, { signal: this.signal });
  }

  round() {
    return this.call === 1 ? "video" : `video-call-${this.call}`;
  }

  render(state, message) {
    this.state = state;
    this.root.dataset.videoState = state;
    this.status.textContent = this.host ? message.replace(/ Music stays connected\.$/, "") : message;
    this.status.title = this.status.textContent;
    const inactive = ["relay-off", "camera-error", "paused"].includes(state);
    this.placeholder.textContent = inactive ? "Camera is off" : "Waiting for the other camera";
    const active = !!this.track?.enabled && this.track.readyState === "live";
    this.camera.textContent = state === "relay-off" ? "Retry" : state === "camera-error" ? "Try again" : active ? "Hide camera" : this.label;
    this.camera.disabled = this.busy || state === "checking" || state === "paused" || !this.credentials && state !== "relay-off";
    this.switcher.hidden = !this.sender || !active;
    this.switcher.disabled = this.busy;
    this.preview.hidden = !active;
    this.remote.hidden = state !== "live";
  }

  async checkRelay() {
    if (this.checking || this.signal.aborted) return;
    this.checking = true;
    this.lastCheck = Date.now();
    try {
      this.credentials = await this.mailbox.credentials();
      if (this.signal.aborted) return;
      clearInterval(this.relayTimer);
      this.render("needs-gesture", `Tap ${this.label} to start your camera. Music stays connected.`);
    } catch {
      if (this.signal.aborted) return;
      this.render("relay-off", DISABLED);
      this.relayTimer ??= setInterval(this.visible, 30000);
    } finally { this.checking = false; }
  }

  // Music timing gates every new camera call. Keep checking, and offer the
  // tap again as soon as a music baseline shows timing is healthy.
  waitForTiming(readyMessage) {
    clearInterval(this.timingTimer);
    this.timingTimer = setInterval(() => {
      if (this.ended || this.peer || this.busy || !this.timingReady()) return;
      clearInterval(this.timingTimer);
      this.timingTimer = null;
      this.render("needs-gesture", readyMessage);
    }, 1000);
  }

  async refreshCameraDevices() {
    const signal = this.signal;
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      if (!signal.aborted) {
        this.cameraDevices = devices.filter(device => device.kind === "videoinput" && device.deviceId);
      }
    } catch {
      // Facing-mode constraints are still available when device IDs are hidden.
    }
  }

  async capture(switchCamera) {
    if (!navigator.mediaDevices?.getUserMedia) throw new DOMException("Unavailable", "NotFoundError");
    const facingMode = this.facing === "environment" ? "user" : "environment";
    const currentId = this.track?.getSettings().deviceId;
    let video = cameraSizes;
    if (switchCamera) {
      const alternative = this.cameraDevices?.find(device => device.deviceId !== currentId);
      // Call getUserMedia in the tap handler's turn. Enumerating here would
      // yield before requesting the camera on browsers that require a gesture.
      video = alternative ? { ...cameraSizes, deviceId: { exact: alternative.deviceId } } :
        { ...cameraSizes, facingMode: { exact: facingMode } };
    }
    try { return await navigator.mediaDevices.getUserMedia({ video, audio: false }); }
    catch (error) {
      if (error.name !== "OverconstrainedError" || switchCamera) throw error;
      return navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    }
  }

  async tap(switchCamera = false) {
    const signal = this.signal;
    if (this.busy || signal.aborted || !this.credentials) return;
    if (!this.peer && !this.timingReady()) {
      this.render("needs-gesture", "Music timing isn't measurable yet. Try again shortly.");
      this.waitForTiming(`Music timing is ready. Tap ${this.label} to start your camera. Music stays connected.`);
      return;
    }
    if (!switchCamera && this.track?.readyState === "live") {
      this.track.enabled = !this.track.enabled;
      this.render(this.peer?.connectionState === "connected" ? "live" : "preview",
        this.track.enabled ? this.cameraStatus() : "Your camera is off. Music stays connected.");
      return;
    }
    clearInterval(this.timingTimer);
    this.timingTimer = null;
    this.busy = true;
    this.camera.disabled = true;
    this.switcher.disabled = true;
    const previous = this.track;
    const previousPreview = this.preview.srcObject;
    let stream;
    let track;
    try {
      stream = await this.capture(switchCamera);
      signal.throwIfAborted();
      track = stream.getVideoTracks()[0];
      if (!track || track.readyState !== "live") throw new DOMException("Unavailable", "NotFoundError");
      this.preview.srcObject = stream;
      this.preview.hidden = false;
      try { await this.preview.play(); }
      catch { throw new DOMException("Preview blocked", "NotAllowedError"); }
      signal.throwIfAborted();
      if (this.sender) await this.sender.replaceTrack(track);
      signal.throwIfAborted();
      this.track = track;
      this.facing = track.getSettings().facingMode;
      previous?.stop();
      track.addEventListener("ended", () => {
        if (this.track === track && !this.busy && !signal.aborted) {
          this.render("camera-error", "Your camera stopped. Tap Try again. Music stays connected.");
        }
      });
      this.busy = false;
      void this.refreshCameraDevices();
      this.render(this.peer?.connectionState === "connected" ? "live" : "preview", this.cameraStatus());
      if (!this.peer && !this.connecting) {
        this.connecting = true;
        void this.connect(stream).catch(() => {
          if (!signal.aborted) this.pause("Video paused. The other camera didn’t connect. Music stays connected.");
        });
      }
    } catch (error) {
      if (track !== this.track) stream?.getTracks().forEach(item => item.stop());
      if (signal.aborted) return;
      if (this.track === previous && previous?.readyState === "live") {
        this.preview.srcObject = previousPreview;
        this.render(this.peer?.connectionState === "connected" ? "live" : "preview",
          "Couldn’t switch cameras. Your current camera is still on. Music stays connected.");
      } else {
        this.track?.stop();
        this.track = null;
        this.preview.srcObject = null;
        this.render("camera-error", cameraErrors[error.name] ?? "Couldn’t start the camera. Try again. Music stays connected.");
      }
    } finally {
      this.busy = false;
      if (!signal.aborted) this.render(this.state, this.status.textContent);
    }
  }

  cameraStatus() {
    return this.peer?.connectionState === "connected" ? "Cameras on." : "Camera preview is on. Waiting for the other person.";
  }

  async connect(stream) {
    const { signal, mailbox } = this;
    const round = this.round();
    const until = this.invite.expiresAt * 1000;
    const offer = this.host ? await mailbox.wait(round, "offer", until, 2500) : null;
    signal.throwIfAborted();
    if (this.host && !offer) throw new Error("The other camera did not connect");
    if (Date.now() + 90000 >= this.credentials.expiresAt) {
      const credentials = await mailbox.credentials();
      signal.throwIfAborted();
      this.credentials = credentials;
    }
    const cert = await RTCPeerConnection.generateCertificate({ name: "ECDSA", namedCurve: "P-256" });
    signal.throwIfAborted();
    if (this.track.readyState !== "live") throw new DOMException("Unavailable", "NotFoundError");
    if (!this.host && !this.timingReady()) throw new Error("Music timing is unavailable");
    const peer = this.peer = new RTCPeerConnection({
      certificates: [cert], iceServers: this.credentials.iceServers, iceTransportPolicy: "all", bundlePolicy: "max-bundle",
    });
    peer.addEventListener("track", event => {
      if (event.track.kind !== "video" || signal.aborted) return;
      this.remote.srcObject = event.streams[0] ?? new MediaStream([event.track]);
      void this.remote.play().catch(() => {});
    });
    peer.addEventListener("connectionstatechange", () => {
      if (!signal.aborted && peer === this.peer) this.connectionChanged();
    });
    if (this.host) await peer.setRemoteDescription({ type: "offer", sdp: cameraDescription(offer) });
    const sender = this.sender = peer.addTrack(this.track, stream);
    preferVp8(peer, sender);
    const parameters = sender.getParameters();
    if (parameters.encodings?.length) {
      parameters.encodings[0].maxBitrate = 350000;
      parameters.encodings[0].maxFramerate = 15;
      await sender.setParameters(parameters);
    }
    await peer.setLocalDescription(await (this.host ? peer.createAnswer() : peer.createOffer()));
    await mailbox.write(round, this.host ? "answer" : "offer", await gatherVideo(peer, signal));
    if (!this.host) {
      const answer = await mailbox.wait(round, "answer", until, 2500);
      signal.throwIfAborted();
      if (!answer) throw new Error("The other camera did not connect");
      await peer.setRemoteDescription({ type: "answer", sdp: cameraDescription(answer) });
    }
    signal.throwIfAborted();
    this.initialComplete = true;
    this.connectionChanged();
  }

  connectionChanged() {
    const signal = this.signal;
    if (signal.aborted) return;
    const state = this.peer.connectionState;
    if (state !== "disconnected") { clearTimeout(this.disconnectTimer); this.disconnectTimer = null; }
    if (state === "failed" || state === "closed") return this.dropped();
    if (state === "disconnected") this.disconnectTimer ??= setTimeout(() => this.dropped(), 10000);
    if (state !== "connected" || !this.initialComplete || this.refresh) return;
    this.render("live", "Cameras on.");
    this.refresh = new VideoRefresh({
      peer: this.peer, mailbox: this.mailbox, credentials: this.credentials, host: this.host, signal,
      prefix: this.call === 1 ? "" : `call-${this.call}-`,
      onExpired: () => {
        if (!signal.aborted) this.pause("Video paused. Video relay expired. Music stays connected.");
      },
    });
    this.refresh.start();
    // Relay bytes count across every camera call; only the per-peer
    // transport counter starts again.
    const budget = this.relayBudget;
    budget.watermark = 0;
    const peer = this.peer;
    this.budgetTimer = setInterval(async () => {
      if (this.statsPending) return;
      this.statsPending = true;
      try {
        const stats = await peer.getStats();
        if (signal.aborted) return;
        const result = videoRelayBudget(stats, budget);
        if (result === "stop") this.pause("Video paused. Video relay budget used. Music stays connected.");
        if (result === "warning") this.render(this.state, "Camera relay is nearly used up. Music stays connected.");
      } catch {
        if (!signal.aborted) this.pause("Video paused. Camera network statistics are unavailable. Music stays connected.");
      } finally { this.statsPending = false; }
    }, 1000);
  }

  dropped() {
    this.pause(`${DROPPED} Music stays connected.`, {
      resumable: true, ready: `${DROPPED} Tap ${this.label} to try again. Music stays connected.`,
    });
  }

  // Resumable pauses (a music timing trip, a dropped camera connection) end
  // only this camera call. Relay expiry, budget and invite expiry are final.
  pause(message, { resumable = false, ready } = {}) {
    // A final pause already explains why video ended; keep that message.
    if (resumable && this.ended) return;
    if (resumable && this.call < MAX_CAMERA_CALLS && Date.now() < this.invite.expiresAt * 1000) {
      this.endCall();
      this.call++;
      this.startCall();
      this.render("paused", message);
      this.waitForTiming(ready ?? `Music timing is steady again. Tap ${this.label} to restart video. Music stays connected.`);
      return;
    }
    if (resumable) message = message.replace(/( Music stays connected\.)?$/, " Camera restarts are used up for this session.$1");
    this.close();
    this.render("paused", message);
  }

  endCall() {
    this.controller.abort();
    this.refresh?.close();
    this.refresh = null;
    clearInterval(this.budgetTimer);
    clearInterval(this.timingTimer);
    clearTimeout(this.disconnectTimer);
    this.disconnectTimer = this.timingTimer = null;
    this.track?.stop();
    this.track = null;
    this.peer?.close();
    this.peer = this.sender = null;
    this.initialComplete = this.connecting = this.statsPending = false;
    this.preview.srcObject = null;
    this.remote.srcObject = null;
  }

  close() {
    if (this.ended) return;
    this.ended = true;
    this.endCall();
    clearInterval(this.relayTimer);
    clearTimeout(this.expiryTimer);
    document.removeEventListener("visibilitychange", this.visible);
    navigator.mediaDevices?.removeEventListener?.("devicechange", this.devicesChanged);
    this.camera.removeEventListener("click", this.buttonClick);
    this.switcher.removeEventListener("click", this.switchClick);
  }

  // RTCPeerConnection.close() takes effect at once and fires no events, and
  // the camera track is stopped here. Late signaling or capture results see
  // the aborted call and release what they made, so music teardown never
  // waits on video.
  async closed() {
    this.close();
  }
}
