import { sdpFingerprint } from "./crypto.js";
import { policySdp } from "./policy.js";

export function videoSdp(sdp, remote = false) {
  const lines = sdp.split(/\r?\n/);
  const media = lines.filter(line => line.startsWith("m="));
  if (media.length !== 1 || !media[0].startsWith("m=video ") ||
      !lines.includes("a=sendrecv") || !lines.some(line => /^a=rtpmap:\d+ VP8\/90000$/i.test(line))) {
    throw new Error("Camera connection must use one two-way VP8 stream");
  }
  return policySdp(lines.filter(line => !/^a=ice-options:.*\btrickle\b/.test(line)).join("\r\n"), {
    remote, allowReviewedAllocations: true,
  });
}

export function cameraDescription(value) {
  if (!value || sdpFingerprint(value.sdp) !== value.pin) throw new Error("Camera identity did not match");
  return videoSdp(value.sdp, true);
}

export function gatherVideo(peer, signal, timeout = 15000) {
  return new Promise((resolve, reject) => {
    const finish = (error) => {
      clearTimeout(timer);
      signal.removeEventListener("abort", abort);
      peer.removeEventListener("icegatheringstatechange", changed);
      if (error) return reject(error);
      try {
        const sdp = videoSdp(peer.localDescription.sdp);
        resolve({ sdp, pin: sdpFingerprint(sdp) });
      } catch (failure) { reject(failure); }
    };
    const changed = () => { if (peer.iceGatheringState === "complete") finish(); };
    const abort = () => finish(new DOMException("Stopped", "AbortError"));
    const timer = setTimeout(() => finish(new Error("Camera couldn't find a network path in time.")), timeout);
    signal.addEventListener("abort", abort, { once: true });
    peer.addEventListener("icegatheringstatechange", changed);
    if (signal.aborted) abort(); else changed();
  });
}

export function preferVp8(peer, sender) {
  const transceiver = peer.getTransceivers().find(item => item.sender === sender);
  const list = globalThis.RTCRtpSender?.getCapabilities?.("video")?.codecs
    .filter(codec => codec.mimeType.toLowerCase() === "video/vp8");
  if (list?.length) transceiver?.setCodecPreferences(list);
  transceiver.direction = "sendrecv";
}
