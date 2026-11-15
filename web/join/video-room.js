import { VideoMailbox } from "./video-mailbox.js";
import { cameraDescription, gatherVideo, preferVp8 } from "./video-peer.js";
import { videoRelayBudget } from "./video-guard.js";
import { VideoRefresh } from "./video-refresh.js";

const DISABLED = "In-room camera is off until video relay is configured. Music stays connected.";
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
    Object.assign(this, { root, invite, host, timingReady });
    this.label = host ? "Show my camera" : "Show camera";
    this.controller = new AbortController();
    this.signal = this.controller.signal;
    this.mailbox = new VideoMailbox(origin, invite, { signal: this.signal });
    this.camera = root.querySelector("[data-camera]");
    this.switcher = root.querySelector("[data-camera-switch]");
    this.preview = root.querySelector("[data-self]");
    this.remote = root.querySelector("[data-remote]");
    this.placeholder = root.querySelector(".video-placeholder");
    this.status = root.querySelector("[data-video-status]");
    this.status.tabIndex = -1;
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

  render(state, message) {
    this.state = state;
    this.root.dataset.videoState = state;
    this.status.textContent = this.host ? message.replace(/ Music stays connected\.$/, "") : message;
    const inactive = ["relay-off", "camera-error", "paused"].includes(state);
    this.placeholder.textContent = inactive ? "Camera is off" : "Waiting for the other camera";
    const active = !!this.track?.enabled && this.track.readyState === "live";
    this.camera.textContent = state === "relay-off" ? "Retry" : state === "camera-error" ? "Try again" : active ? "Hide camera" : this.label;
    const cameraUnavailable = state === "checking" || state === "paused" || !this.credentials && state !== "relay-off";
    const switchHidden = !this.initialComplete || !active;
    const focused = document.activeElement;
    if (cameraUnavailable && (focused === this.camera || focused === this.switcher)) {
      this.status.focus();
    } else if (switchHidden && focused === this.switcher) {
      this.camera.focus();
    }
    this.camera.disabled = cameraUnavailable;
    this.switcher.hidden = switchHidden;
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

  async refreshCameraDevices() {
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      if (!this.signal.aborted) {
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
      this.signal.throwIfAborted();
      return navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    }
  }

  setCapturePending(pending) {
    this.busy = pending;
    // A native disabled button loses keyboard focus during the permission prompt.
    for (const button of [this.camera, this.switcher]) {
      if (pending) button.setAttribute("aria-disabled", "true");
      else button.removeAttribute("aria-disabled");
    }
  }

  async tap(switchCamera = false) {
    if (this.busy || this.signal.aborted || !this.credentials || (switchCamera && !this.initialComplete)) return;
    if (!this.peer && !this.timingReady()) {
      this.render("needs-gesture", "Music timing isn't measurable yet. Try again shortly.");
      return;
    }
    if (!switchCamera && this.track?.readyState === "live") {
      this.track.enabled = !this.track.enabled;
      this.render(this.peer?.connectionState === "connected" ? "live" : "preview",
        this.track.enabled ? this.cameraStatus() : "Your camera is off. Music stays connected.");
      return;
    }
    if (this.peer && !this.initialComplete) return;
    this.setCapturePending(true);
    const previous = this.track;
    const previousPreview = this.preview.srcObject;
    let stream;
    let track;
    try {
      stream = await this.capture(switchCamera);
      this.signal.throwIfAborted();
      this.pendingCapture = stream;
      track = stream.getVideoTracks()[0];
      if (!track || track.readyState !== "live") throw new DOMException("Unavailable", "NotFoundError");
      this.preview.srcObject = stream;
      this.preview.hidden = false;
      try { await this.preview.play(); }
      catch { throw new DOMException("Preview blocked", "NotAllowedError"); }
      this.signal.throwIfAborted();
      if (this.sender) await this.sender.replaceTrack(track);
      this.signal.throwIfAborted();
      this.track = track;
      this.pendingCapture = null;
      this.facing = track.getSettings().facingMode;
      previous?.stop();
      track.addEventListener("ended", () => {
        if (this.track === track && !this.busy && !this.signal.aborted) {
          this.render("camera-error", "Your camera stopped. Tap Try again. Music stays connected.");
        }
      });
      void this.refreshCameraDevices();
      this.render(this.peer?.connectionState === "connected" ? "live" : "preview", this.cameraStatus());
      if (!this.peer && !this.connecting) {
        this.connecting = true;
        void this.connect().catch(() => {
          if (!this.signal.aborted) this.pause("Video paused. The other camera didn’t connect. Music stays connected.");
        });
      }
    } catch (error) {
      // A pending stream may have already been released by close().
      stream?.getTracks().forEach(item => { if (item !== this.track && item.readyState === "live") item.stop(); });
      this.pendingCapture = null;
      if (this.signal.aborted) return;
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
      if (!this.signal.aborted) {
        this.setCapturePending(false);
        this.render(this.state, this.status.textContent);
      }
    }
  }

  cameraStatus() {
    return this.peer?.connectionState === "connected" ? "Cameras on." : "Camera preview is on. Waiting for the other person.";
  }

  async connect() {
    const until = this.invite.expiresAt * 1000;
    const offer = this.host ? await this.mailbox.wait("video", "offer", until, 2500) : null;
    if (this.host && !offer) throw new Error("The other camera did not connect");
    if (Date.now() + 90000 >= this.credentials.expiresAt) this.credentials = await this.mailbox.credentials();
    const cert = await RTCPeerConnection.generateCertificate({ name: "ECDSA", namedCurve: "P-256" });
    this.signal.throwIfAborted();
    if (this.track.readyState !== "live") throw new DOMException("Unavailable", "NotFoundError");
    if (!this.host && !this.timingReady()) throw new Error("Music timing is unavailable");
    const peer = this.peer = new RTCPeerConnection({
      certificates: [cert], iceServers: this.credentials.iceServers, iceTransportPolicy: "all", bundlePolicy: "max-bundle",
    });
    peer.addEventListener("track", event => {
      if (this.peer !== peer || event.track.kind !== "video" || this.signal.aborted) return;
      this.remote.srcObject = event.streams[0] ?? new MediaStream([event.track]);
      void this.remote.play().catch(() => {});
    });
    peer.addEventListener("connectionstatechange", () => {
      if (this.peer === peer) this.connectionChanged();
    });
    if (this.host) await peer.setRemoteDescription({ type: "offer", sdp: cameraDescription(offer) });
    this.signal.throwIfAborted();
    this.sender = peer.addTrack(this.track, this.preview.srcObject);
    preferVp8(peer, this.sender);
    const parameters = this.sender.getParameters();
    if (parameters.encodings?.length) {
      parameters.encodings[0].maxBitrate = 350000;
      parameters.encodings[0].maxFramerate = 15;
      await this.sender.setParameters(parameters);
    }
    await peer.setLocalDescription(await (this.host ? peer.createAnswer() : peer.createOffer()));
    let description;
    try {
      description = await gatherVideo(peer, this.signal);
    } catch {
      if (!this.signal.aborted) {
        // Gathering failed before any write. This side can still use its
        // original write-once slot on the next explicit camera tap.
        this.peer = null;
        this.sender = null;
        this.connecting = false;
        clearTimeout(this.disconnectTimer);
        this.disconnectTimer = null;
        peer.close();
        const track = this.track;
        this.track = null;
        track?.stop();
        this.preview.srcObject = null;
        this.remote.srcObject = null;
        this.render("camera-error", "Camera couldn't find a network path. Tap Try again. Music stays connected.");
      }
      return;
    }
    await this.mailbox.write("video", this.host ? "answer" : "offer", description);
    if (!this.host) {
      const answer = await this.mailbox.wait("video", "answer", until, 2500);
      if (!answer) throw new Error("The other camera did not connect");
      await peer.setRemoteDescription({ type: "answer", sdp: cameraDescription(answer) });
    }
    this.initialComplete = true;
    this.connectionChanged();
  }

  connectionChanged() {
    if (this.signal.aborted) return;
    const state = this.peer.connectionState;
    if (state !== "disconnected") { clearTimeout(this.disconnectTimer); this.disconnectTimer = null; }
    if (state === "failed" || state === "closed") this.pause("Video paused. The camera connection dropped. Music stays connected.");
    if (state === "disconnected") this.disconnectTimer ??= setTimeout(() => this.pause("Video paused. The camera connection dropped. Music stays connected."), 10000);
    if (state !== "connected" || !this.initialComplete || this.refresh) return;
    this.render("live", "Cameras on.");
    this.refresh = new VideoRefresh({
      peer: this.peer, mailbox: this.mailbox, credentials: this.credentials, host: this.host, signal: this.signal,
      onExpired: () => this.pause("Video paused. Video relay expired. Music stays connected."),
    });
    this.refresh.start();
    const budget = { counted: 0, watermark: 0 };
    this.budgetTimer = setInterval(async () => {
      if (this.statsPending) return;
      this.statsPending = true;
      try {
        const stats = await this.peer.getStats();
        if (this.signal.aborted) return;
        const result = videoRelayBudget(stats, budget);
        if (result === "stop") this.pause("Video paused. Video relay budget used. Music stays connected.");
        if (result === "warning") this.render(this.state, "Camera relay is nearly used up. Music stays connected.");
      } catch { this.pause("Video paused. Camera network statistics are unavailable. Music stays connected."); }
      finally { this.statsPending = false; }
    }, 1000);
  }

  pause(message) {
    this.close();
    this.render("paused", message);
  }

  close() {
    if (this.signal.aborted) return;
    this.controller.abort();
    this.setCapturePending(false);
    this.refresh?.close();
    clearInterval(this.relayTimer);
    clearInterval(this.budgetTimer);
    clearTimeout(this.disconnectTimer);
    clearTimeout(this.expiryTimer);
    document.removeEventListener("visibilitychange", this.visible);
    navigator.mediaDevices?.removeEventListener?.("devicechange", this.devicesChanged);
    this.camera.removeEventListener("click", this.buttonClick);
    this.switcher.removeEventListener("click", this.switchClick);
    this.pendingCapture?.getTracks().forEach(track => track.stop());
    this.pendingCapture = null;
    this.track?.stop();
    this.track = null;
    this.peer?.close();
    this.preview.srcObject = null;
    this.remote.srcObject = null;
  }

  async closed() {
    this.close();
    if (!this.peer || this.peer.iceConnectionState === "closed" || this.peer.connectionState === "closed") return;
    await new Promise(resolve => {
      const done = () => {
        this.peer.removeEventListener("iceconnectionstatechange", changed);
        this.peer.removeEventListener("connectionstatechange", changed);
        clearTimeout(timer);
        resolve();
      };
      const changed = () => {
        if (this.peer.iceConnectionState === "closed" || this.peer.connectionState === "closed") done();
      };
      const timer = setTimeout(done, 1000);
      this.peer.addEventListener("iceconnectionstatechange", changed);
      this.peer.addEventListener("connectionstatechange", changed);
      changed();
    });
  }
}
