import { acquireMusicStream } from "./audio.js";

/** RMS above this counts as audible input for the pre-join check. */
export const MIC_HEARD_THRESHOLD = 0.012;

export function micCheckFailure(error) {
  if (error?.name === "NotFoundError") {
    return {
      message: "No microphone found - plug one in and press Try again.",
      showRetry: true,
    };
  }
  if (error?.name === "NotAllowedError") {
    return {
      message:
        "Microphone access is blocked. Click the lock or site icon in your browser’s address bar, allow the microphone, then press Try again.",
      showRetry: true,
    };
  }
  return {
    message: error?.message || "Could not use your microphone. Press Try again.",
    showRetry: true,
  };
}

export function hasAudioInputDevice(devices) {
  return devices.some((device) => device.kind === "audioinput");
}

export function micLevelRms(analyser) {
  const samples = new Float32Array(analyser.fftSize);
  analyser.getFloatTimeDomainData(samples);
  let sum = 0;
  for (const value of samples) sum += value * value;
  return Math.sqrt(sum / samples.length);
}

export function startMicLevelMonitor({
  audioContext,
  stream,
  threshold = MIC_HEARD_THRESHOLD,
  onLevel,
  onHeard,
  signal,
}) {
  const source = audioContext.createMediaStreamSource(stream);
  const analyser = audioContext.createAnalyser();
  analyser.fftSize = 2048;
  source.connect(analyser);
  let heard = false;
  let frame = 0;
  const tick = () => {
    if (signal?.aborted) return;
    const level = micLevelRms(analyser);
    onLevel(level);
    if (!heard && level >= threshold) {
      heard = true;
      onHeard();
    }
    frame = requestAnimationFrame(tick);
  };
  frame = requestAnimationFrame(tick);
  return () => {
    cancelAnimationFrame(frame);
    source.disconnect();
    analyser.disconnect();
  };
}

export async function beginMicCheck(mediaDevices) {
  const devices = await mediaDevices.enumerateDevices();
  if (hasAudioInputDevice(devices)) {
    return acquireMusicStream(mediaDevices);
  }
  try {
    return await acquireMusicStream(mediaDevices);
  } catch (error) {
    if (error.name === "NotFoundError") throw error;
    throw error;
  }
}
