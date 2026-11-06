import { readInvite } from "./crypto.js";
import { acquireMusicStream } from "./audio.js";
import { WebJoinSession } from "./session.js";
import { RELAY_ORIGIN } from "./config.js";
import {
  beginMicCheck,
  micCheckFailure,
  startMicLevelMonitor,
} from "./prejoin.js";
import { JOIN_SIGNAL, applyJoinFailure, clearJoinFailure, mapJoinError } from "./errors.js";

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
  clearJoinFailure(failureElements(), "Your host invited you to join.");
} catch (error) {
  const view = mapJoinError(error);
  view.showRetry = false;
  applyJoinFailure(view, failureElements());
  $("status").dataset.failure = "true";
}

function showAudioSettings(stream, warnings = []) {
  const sampleRate = stream?.getAudioTracks()[0]?.getSettings().sampleRate;
  $("audio-quality").textContent =
    sampleRate > 0 && sampleRate < 32000
      ? "Your mic is using voice-quality audio. Music may sound less clear."
      : "";
  $("warnings").textContent = warnings.join("\n");
}

async function showMicDevices(track, signal) {
  const source = $("mic-source");
  source.textContent = track.label ? `Using ${track.label}` : "Microphone connected.";
  source.hidden = false;
  const devices = await navigator.mediaDevices.enumerateDevices().catch(() => []);
  if (signal.aborted) return;
  const choices = devices.filter(device => device.kind === "audioinput" && device.label &&
    device.deviceId && !["default", "communications"].includes(device.deviceId));
  const select = $("mic-device");
  const selected = select.value;
  select.replaceChildren(new Option("Browser default", ""), ...choices.map(device => new Option(device.label, device.deviceId)));
  if (choices.some(device => device.deviceId === selected)) select.value = selected;
  $("mic-picker").hidden = choices.length < 2;
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
    showAudioSettings();
  }
  $("mic-progress").textContent = "";
  $("mic-source").hidden = true;
  $("mic-error").textContent = "";
  $("mic-retry").hidden = true;
  $("check-mic").disabled = checkSkipped || micHeard;
}

function resetMicCheck() {
  stopMicMonitoring(true);
  $("check-mic").disabled = false;
}

function watchMicrophone(track, signal, onLost) {
  track.addEventListener("ended", onLost, { once: true, signal });
  if (track.readyState === "live") return true;
  onLost();
  return false;
}

function joinReady() {
  if (!invite || starting || session) return false;
  if (checkSkipped) return $("headphones").checked;
  return micHeard && $("headphones").checked;
}

const refresh = () => {
  $("prejoin").hidden = !invite || starting || !!session;
  $("join").disabled = !joinReady();
  $("skip-check").disabled = !invite || starting || !!session || checkSkipped;
  $("mic-device").disabled = starting || !!session || checkRunning;
  $("check-mic").disabled =
    !invite || starting || !!session || checkRunning || checkSkipped || micHeard || !!stopMeter;
};

function showMicError(error) {
  const failure = micCheckFailure(error?.cause ?? error);
  $("mic-progress").textContent = "";
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
  refresh();
  $("mic-error").textContent = "";
  prejoinAbort = new AbortController();
  const { signal } = prejoinAbort;
  try {
    if (!context || context.state === "closed") {
      context = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
    }
    await context.resume();
    if (signal.aborted) return;
    $("mic-progress").textContent = "Allow microphone access in your browser.";
    const capture = await beginMicCheck(navigator.mediaDevices, $("mic-device").value);
    if (signal.aborted) {
      capture.stream.getTracks().forEach((track) => track.stop());
      return;
    }
    prejoinStream = capture.stream;
    prejoinWarnings = capture.warnings;
    const track = prejoinStream.getAudioTracks()[0];
    if (!watchMicrophone(track, signal, () => {
      stopMicMonitoring(true);
      $("mic-device").value = "";
      showMicError(new Error("Microphone disconnected. Reconnect it or choose another input, then try again."));
    })) return;
    showAudioSettings(prejoinStream, prejoinWarnings);
    void showMicDevices(track, signal);
    $("mic-progress").textContent = "Speak or play a note to check your level.";
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
        $("mic-progress").textContent = "";
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
$("mic-device").addEventListener("change", () => {
  checkSkipped = false;
  void runMicCheck();
});
$("skip-check").addEventListener("click", () => {
  if (!invite || session || checkSkipped) return;
  resetMicCheck();
  checkSkipped = true;
  $("mic-progress").textContent = "Mic check skipped. Your browser may ask for access when you join.";
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
    const view = mapJoinError(error);
    if (view.needsFreshInvite && !view.showRetry) {
      invite = undefined;
    }
    applyJoinFailure(view, elements);
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
      prejoinAbort?.abort();
      prejoinAbort = null;
      stopMeter?.();
      stopMeter = null;
      showAudioSettings(stream, prejoinWarnings);
    } else {
      $("status").textContent = "Allow microphone access to play with your host.";
      const capture = await acquireMusicStream(navigator.mediaDevices, $("mic-device").value);
      stream = capture.stream;
      if (!isCurrent()) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      showAudioSettings(stream, capture.warnings);
    }
    if (!watchMicrophone(stream.getAudioTracks()[0], attempt.signal, () => {
      leave(undefined, new Error(JOIN_SIGNAL.MICROPHONE_ENDED));
    })) return;
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
window.addEventListener("hashchange", () => window.location.reload());
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
