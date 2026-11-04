import { readInvite } from "./crypto.js";
import { acquireMusicStream } from "./audio.js";
import { WebJoinSession } from "./session.js";
import { RELAY_ORIGIN } from "./config.js";
import {
  beginMicCheck,
  micCheckFailure,
  startMicLevelMonitor,
} from "./prejoin.js";
import { applyJoinFailure, clearJoinFailure, mapJoinError } from "./errors.js";

const $ = (id) => document.getElementById(id);
const failureElements = () => ({
  status: $("status"),
  hint: $("error-hint"),
  retry: $("retry"),
  join: $("join"),
  leave: $("leave"),
});

let invite;
let session;
let context;
let starting = false;
let activeAttempt;
let prejoinStream;
let prejoinWarnings = [];
let stopMeter = null;
let micHeard = false;
let checkSkipped = false;
let checkRunning = false;
let prejoinAbort;

try {
  invite = readInvite(location.hash);
  clearJoinFailure(
    failureElements(),
    "Your host invited you. Check your microphone, confirm headphones, then join.",
  );
  $("prejoin").hidden = false;
} catch (error) {
  const view = mapJoinError(error);
  view.showRetry = false;
  applyJoinFailure(view, failureElements());
  $("status").dataset.failure = "true";
  $("prejoin").hidden = true;
}

function stopMicMonitoring(clearHeard = false) {
  prejoinAbort?.abort();
  prejoinAbort = null;
  stopMeter?.();
  stopMeter = null;
  prejoinStream?.getTracks().forEach((track) => track.stop());
  prejoinStream = null;
  prejoinWarnings = [];
  checkRunning = false;
  $("mic-meter-fill").style.width = "0%";
  $("mic-meter").setAttribute("aria-valuenow", "0");
  if (clearHeard) {
    micHeard = false;
    $("mic-heard").hidden = true;
  }
  $("mic-error").textContent = "";
  $("mic-retry").hidden = true;
  $("check-mic").disabled = checkSkipped || micHeard;
}

function resetMicCheck() {
  stopMicMonitoring(true);
  $("check-mic").disabled = false;
}

function joinReady() {
  if (!invite || starting || session) return false;
  if (checkSkipped) return $("headphones").checked;
  return micHeard && $("headphones").checked;
}

const refresh = () => {
  $("join").disabled = !joinReady();
  $("skip-check").disabled = !invite || starting || !!session || checkSkipped;
  $("check-mic").disabled =
    !invite || starting || !!session || checkRunning || checkSkipped || micHeard || !!stopMeter;
};

function showMicError(error) {
  const failure = micCheckFailure(error);
  $("mic-error").textContent = failure.message;
  $("mic-retry").hidden = !failure.showRetry;
  $("check-mic").disabled = false;
  checkRunning = false;
  refresh();
}

async function runMicCheck() {
  if (!invite || checkRunning || starting || session) return;
  resetMicCheck();
  checkRunning = true;
  $("check-mic").disabled = true;
  $("mic-error").textContent = "";
  prejoinAbort = new AbortController();
  const { signal } = prejoinAbort;
  try {
    if (!context || context.state === "closed") {
      context = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
    }
    await context.resume();
    if (signal.aborted) return;
    $("status").textContent = "Allow microphone access so we can check your level.";
    const capture = await beginMicCheck(navigator.mediaDevices);
    if (signal.aborted) {
      capture.stream.getTracks().forEach((track) => track.stop());
      return;
    }
    prejoinStream = capture.stream;
    prejoinWarnings = capture.warnings;
    $("status").textContent = "Play or speak into your mic. We’ll tell you when we hear you.";
    stopMeter = startMicLevelMonitor({
      audioContext: context,
      stream: prejoinStream,
      signal,
      onLevel: (level) => {
        if (signal.aborted) return;
        const percent = Math.min(100, Math.round(level * 400));
        $("mic-meter-fill").style.width = `${percent}%`;
        $("mic-meter").setAttribute("aria-valuenow", String(percent));
      },
      onHeard: () => {
        if (signal.aborted) return;
        micHeard = true;
        $("mic-heard").hidden = false;
        refresh();
      },
    });
    checkRunning = false;
    $("check-mic").disabled = true;
    refresh();
  } catch (error) {
    if (!signal.aborted) showMicError(error);
  }
}

$("headphones").addEventListener("change", refresh);
$("check-mic").addEventListener("click", () => runMicCheck());
$("mic-retry").addEventListener("click", () => runMicCheck());
$("skip-check").addEventListener("click", () => {
  if (!invite || session || checkSkipped) return;
  resetMicCheck();
  checkSkipped = true;
  $("status").textContent = "Check skipped. Confirm headphones, then join when ready.";
  refresh();
});

function leave(message = "You left the session.", error) {
  activeAttempt?.abort();
  activeAttempt = null;
  session?.close();
  session = null;
  starting = false;
  $("host-audio").pause();
  $("host-audio").srcObject = null;
  stopMicMonitoring(false);
  context?.close().catch(() => {});
  context = null;
  $("leave").hidden = true;
  const elements = failureElements();
  if (error) {
    applyJoinFailure(mapJoinError(error), elements);
    elements.status.dataset.failure = "true";
  } else {
    delete elements.status.dataset.failure;
    clearJoinFailure(elements, message);
  }
  refresh();
}

async function startJoin() {
  if (!joinReady() || starting || session) return;
  starting = true;
  refresh();
  delete $("status").dataset.failure;
  clearJoinFailure(failureElements(), "Connecting…");
  $("leave").hidden = false;
  const attempt = (activeAttempt = new AbortController());
  const isCurrent = () => activeAttempt === attempt && !attempt.signal.aborted;
  let stream;
  const cancel = () => attempt.abort();
  $("leave").addEventListener("click", cancel, { once: true });
  try {
    if (!context || context.state === "closed") {
      context = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
    }
    await context.resume();
    if (!isCurrent()) return;
    if (prejoinStream?.getAudioTracks().some((track) => track.readyState === "live")) {
      stream = prejoinStream;
      prejoinStream = null;
      stopMeter?.();
      stopMeter = null;
      $("warnings").textContent = prejoinWarnings.join("\n");
    } else {
      $("status").textContent = "Allow microphone access to play with your host.";
      const capture = await acquireMusicStream(navigator.mediaDevices);
      stream = capture.stream;
      if (!isCurrent()) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      $("warnings").textContent = capture.warnings.join("\n");
    }
    const joinedSession = new WebJoinSession({
      origin: RELAY_ORIGIN,
      invite,
      stream,
      audioContext: context,
      onStatus: (message) => {
        if (!isCurrent()) return;
        $("status").textContent = message;
      },
      onLatency: (milliseconds) => {
        if (!isCurrent()) return;
        $("latency").textContent =
          milliseconds === null
            ? "Estimated listening delay: unavailable in this browser."
            : `Estimated listening delay: ${Math.round(milliseconds)} ms (network + playback; excludes capture and host bridge).`;
      },
      onTrack: (remote) => {
        if (!isCurrent()) return;
        $("host-audio").srcObject = remote;
        $("host-audio").play().catch(() => {
          if (isCurrent()) {
            leave("Playback could not start. Tap Join again.");
          }
        });
      },
      onError: (error) => {
        if (isCurrent()) leave(undefined, error);
      },
    });
    session = joinedSession;
    await joinedSession.join();
    if (!isCurrent()) return;
    clearJoinFailure(failureElements(), $("status").textContent);
    starting = false;
    refresh();
  } catch (error) {
    stream?.getTracks().forEach((track) => track.stop());
    if (isCurrent()) {
      if (error.name === "AbortError") {
        leave(
          undefined,
          new Error(
            "Joining timed out or was interrupted. Check your connection and microphone, then try Join again.",
          ),
        );
      } else {
        leave(undefined, error);
      }
    }
  } finally {
    $("leave").removeEventListener("click", cancel);
    if (!isCurrent()) {
      stream?.getTracks().forEach((track) => track.stop());
    }
  }
}

$("join").addEventListener("click", () => {
  void startJoin();
});
$("retry").addEventListener("click", () => {
  void startJoin();
});
$("leave").addEventListener("click", () => leave());
window.addEventListener("pagehide", () => leave());
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && context?.state === "suspended") {
    const attempt = activeAttempt;
    context.resume().catch(() => {
      if (attempt && !attempt.signal.aborted && activeAttempt === attempt) {
        leave("Tap Join again to resume audio.");
      }
    });
  }
});
refresh();
