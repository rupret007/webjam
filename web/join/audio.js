export const MUSIC_CONSTRAINTS = Object.freeze({
  echoCancellation: false,
  noiseSuppression: false,
  autoGainControl: false,
  channelCount: 2,
  sampleRate: 48000,
});

export function constraintWarnings(settings) {
  return Object.entries(MUSIC_CONSTRAINTS).filter(([key, expected]) => settings[key] !== expected).map(([key, expected]) => {
    const actual = settings[key] === undefined ? "not reported" : String(settings[key]);
    return `${key}: requested ${expected}, actual ${actual}`;
  });
}

export async function acquireMusicStream(mediaDevices, deviceId = "") {
  let stream;
  let fallback = false;
  const device = deviceId ? { deviceId: { exact: deviceId } } : {};
  try {
    stream = await mediaDevices.getUserMedia({ video: false, audio: { ...MUSIC_CONSTRAINTS, ...device } });
  } catch (error) {
    if (error.name === "NotAllowedError") {
      throw new Error("Microphone access was blocked. Allow the mic in your browser settings, then tap Join again.", { cause: error });
    }
    if (!["OverconstrainedError", "NotSupportedError"].includes(error.name)) throw error;
    if (error.constraint === "deviceId") throw error;
    fallback = true;
    stream = await mediaDevices.getUserMedia({ video: false, audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, ...device } });
  }
  const tracks = stream.getAudioTracks();
  if (tracks.length !== 1) {
    stream.getTracks().forEach((track) => track.stop());
    throw new Error("A single audio input is required");
  }
  const settings = tracks[0].getSettings();
  return { stream, warnings: [...(fallback ? ["Your browser needed simpler microphone constraints."] : []), ...constraintWarnings(settings)] };
}

export function musicSdp(sdp, ptime = 20) {
  if (![10, 20].includes(ptime)) throw new Error("Unsupported packet time");
  const lines = sdp.replace(/\r\n/g, "\n").trimEnd().split("\n");
  let audio = false;
  let section = [];
  const output = [];
  const flush = () => {
    if (audio) {
      const opus = section.find((line) => /^a=rtpmap:\d+ opus\/48000(?:\/2)?$/i.test(line));
      if (opus) {
        const payload = opus.match(/^a=rtpmap:(\d+)/)[1];
        const prefix = `a=fmtp:${payload} `;
        const index = section.findIndex((line) => line.startsWith(prefix));
        const parameters = new Map((index < 0 ? "" : section[index].slice(prefix.length)).split(";").filter(Boolean).map((item) => {
          const [name, ...rest] = item.trim().split("=");
          return [name.toLowerCase(), rest.join("=")];
        }));
        for (const [name, value] of Object.entries({ stereo: "1", "sprop-stereo": "1", maxaveragebitrate: "128000", usedtx: "0", useinbandfec: "1" })) parameters.set(name, value);
        const fmtp = prefix + [...parameters].map(([name, value]) => `${name}=${value}`).join(";");
        if (index < 0) section.push(fmtp); else section[index] = fmtp;
        section = section.filter((line) => !line.startsWith("a=ptime:"));
        section.push(`a=ptime:${ptime}`);
      }
    }
    output.push(...section);
    section = [];
  };
  for (const line of lines) {
    if (line.startsWith("m=")) {
      flush();
      audio = line.startsWith("m=audio ");
    }
    section.push(line);
  }
  flush();
  return `${output.join("\r\n")}\r\n`;
}

export function tuneReceiver(receiver, targetMs = 20) {
  if ("jitterBufferTarget" in receiver) {
    try { receiver.jitterBufferTarget = targetMs; } catch { /* Optional browser support. */ }
  }
}

const JITTER_TARGET_MIN_MS = 20;
const JITTER_TARGET_MAX_MS = 120;
const JITTER_TARGET_DEFAULT_MS = 20;

export function adaptJitterBufferTarget(stats, state = {}) {
  let rttMs;
  let jitterMs = 0;
  let lossRate = 0;
  let bufferMs = JITTER_TARGET_DEFAULT_MS;
  for (const report of stats.values()) {
    if (report.type === "candidate-pair" && (report.nominated || report.selected) && report.state === "succeeded" && Number.isFinite(report.currentRoundTripTime)) {
      rttMs = report.currentRoundTripTime * 1000;
    }
    if (report.type === "inbound-rtp" && (report.kind === "audio" || report.mediaType === "audio")) {
      if (report.packetsReceived > 0 && Number.isFinite(report.packetsLost)) {
        lossRate = report.packetsLost / (report.packetsReceived + report.packetsLost);
      }
      if (report.jitterBufferEmittedCount > 0 && Number.isFinite(report.jitterBufferDelay)) {
        bufferMs = (report.jitterBufferDelay / report.jitterBufferEmittedCount) * 1000;
      }
      if (Number.isFinite(report.jitter)) {
        jitterMs = Math.max(jitterMs, report.jitter * 1000);
      }
    }
  }
  const current = Number.isFinite(state.targetMs) ? state.targetMs : JITTER_TARGET_DEFAULT_MS;
  let target = current;
  const stress = (rttMs ?? 0) * 0.25 + jitterMs * 2 + lossRate * 80 + Math.max(0, bufferMs - current);
  if (stress > 25 && current < JITTER_TARGET_MAX_MS) {
    target = Math.min(JITTER_TARGET_MAX_MS, current + 10);
  } else if (stress < 8 && current > JITTER_TARGET_MIN_MS) {
    target = Math.max(JITTER_TARGET_MIN_MS, current - 5);
  }
  const now = Date.now();
  const lastChange = Number.isFinite(state.lastChangeAt) ? state.lastChangeAt : 0;
  if (target !== current && now - lastChange < 5000) {
    target = current;
  }
  return { targetMs: target, lastChangeAt: target !== current ? now : lastChange, rttMs, jitterMs, lossRate, bufferMs };
}

export function applyAdaptiveReceiverTarget(receiver, adaptation) {
  if (!receiver || !adaptation || !Number.isFinite(adaptation.targetMs)) return adaptation;
  tuneReceiver(receiver, adaptation.targetMs);
  return adaptation;
}

export function latencyMs(stats, baseLatency = 0) {
  let rtt;
  let buffer;
  for (const report of stats.values()) {
    if (report.type === "candidate-pair" && (report.nominated || report.selected) && report.state === "succeeded" && Number.isFinite(report.currentRoundTripTime)) rtt = report.currentRoundTripTime;
    if (report.type === "inbound-rtp" && (report.kind === "audio" || report.mediaType === "audio") && report.jitterBufferEmittedCount > 0 && Number.isFinite(report.jitterBufferDelay)) buffer = report.jitterBufferDelay / report.jitterBufferEmittedCount;
  }
  if (rtt === undefined || buffer === undefined) return null;
  return (rtt / 2 + buffer + (Number.isFinite(baseLatency) ? baseLatency : 0)) * 1000;
}
