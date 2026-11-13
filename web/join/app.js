import { readInvite } from "./crypto.js";
import { acquireMusicStream, closeAudioContext } from "./audio.js";
import { WebJoinSession } from "./session.js";
import { RELAY_ORIGIN } from "./config.js";
import {
  micCheckFailure,
  startMicLevelMonitor,
} from "./prejoin.js";
import { JOIN_SIGNAL, applyJoinFailure, clearJoinFailure, mapJoinError } from "./errors.js";

const $ = (id) => document.getElementById(id);
const listen = $("listen");
const listenActions = $("listen-actions");
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
let retryListening = false;
let activeAttempt;
let prejoinStream;
let prejoinWarnings = [];
let stopMeter = null;
let micHeard = false;
let checkSkipped = false;
let checkRunning = false;
let prejoinAbort;
let accessTimer = null;
let inviteExpiryTimer = null;

function expireUnusedInvite() {
  if (!invite || session || Date.now() < invite.expiresAt * 1000) return false;
  const focused = document.activeElement;
  leave(undefined, new Error(JOIN_SIGNAL.INVITE_EXPIRED));
  if ($("prejoin").contains(focused) || [ $("listen"), $("join"), $("retry"), $("leave") ].includes(focused)) {
    $("status").focus({ preventScroll: true });
  }
  return true;
}

function scheduleUnusedInviteExpiry() {
  clearTimeout(inviteExpiryTimer);
  if (!invite || session) return;
  inviteExpiryTimer = setTimeout(() => {
    if (!expireUnusedInvite()) scheduleUnusedInviteExpiry();
  }, Math.max(0, invite.expiresAt * 1000 - Date.now()));
}

function clearAccessTime() {
  clearInterval(accessTimer);
  accessTimer = null;
  $("access-window").hidden = true;
  $("access-time").textContent = "";
  $("access-warning").textContent = "";
  $("access-warning").hidden = true;
}

function showAccessTime(expiresAt, isCurrent) {
  clearAccessTime();
  if (!Number.isFinite(expiresAt) || expiresAt <= 0) return;
  const update = () => {
    if (!isCurrent()) { clearAccessTime(); return false; }
    const seconds = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
    const minutes = Math.floor(seconds / 60);
    const remainder = String(seconds % 60).padStart(2, "0");
    $("access-time").textContent = `Browser access ends in ${minutes}:${remainder}.`;
    const warning = seconds === 0
      ? "Browser access has ended. Ask your host for a fresh invite."
      : seconds <= 60
        ? "Less than a minute of browser access remains. Ask your host for a fresh invite to continue."
        : "";
    // Announce each warning once, rather than reading every countdown tick.
    if ($("access-warning").textContent !== warning) $("access-warning").textContent = warning;
    $("access-warning").hidden = !warning;
    $("access-window").hidden = false;
    return true;
  };
  if (update()) accessTimer = setInterval(update, 1000);
}

try {
  invite = readInvite(location.hash);
  clearJoinFailure(failureElements(), "Your host invited you to join.");
} catch (error) {
  const view = mapJoinError(error);
  if (view.kind !== "invite_expired") {
    view.hint = "Ask your host for a new browser invite and open the full link.";
  }
  view.showRetry = false;
  view.needsFreshInvite = true;
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

async function showMicDevices(signal) {
  const devices = await navigator.mediaDevices.enumerateDevices().catch(() => []);
  if (signal.aborted) return;
  const choices = devices.filter(device => device.kind === "audioinput" && device.label &&
    device.deviceId && !["default", "communications"].includes(device.deviceId));
  const select = $("mic-device");
  const selected = select.value;
  select.replaceChildren(new Option("Browser default", ""), ...choices.map(device => new Option(device.label, device.deviceId)));
  if (choices.some(device => device.deviceId === selected)) select.value = selected;
  $("mic-picker").hidden = choices.length === 0;
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

function focusNextAudioCheckControl() {
  $(joinReady() ? "join" : "headphones").focus();
}

const refresh = () => {
  $("prejoin").hidden = !invite || starting || !!session || (retryListening && !$("retry").hidden);
  if (listen && listenActions) {
    listenActions.hidden = $("join").hidden || !invite || starting || !!session;
    listen.disabled = listenActions.hidden;
  }
  $("join").disabled = !joinReady();
  const guidance = $("join-guidance");
  if (guidance) {
    const message = starting
      ? "Connecting to your session…"
      : !micHeard && !checkSkipped
        ? "Check your microphone first."
        : !$("headphones").checked
          ? "Confirm your headphones to enable Join."
          : "Ready. Join to share your audio with the session.";
    if (guidance.textContent !== message) guidance.textContent = message;
  }
  $("skip-check").disabled = !invite || starting || !!session || checkSkipped;
  $("mic-device").disabled = starting || !!session || checkRunning;
  $("check-mic").disabled =
    !invite || starting || !!session || checkRunning || checkSkipped || micHeard || !!stopMeter;
};

function showMicError(error, deviceDisconnected = false) {
  const activeControl = document.activeElement;
  const restoreFocus = activeControl === $("mic-progress") ||
    (deviceDisconnected && activeControl === $("join"));
  stopMicMonitoring(true);
  void closeAudioContext(context);
  context = null;
  const cause = error?.cause ?? error;
  const failure = micCheckFailure(cause);
  $("mic-progress").textContent = "";
  $("mic-error").textContent = failure.message;
  $("mic-retry").hidden = !failure.showRetry;
  $("check-mic").disabled = false;
  checkRunning = false;
  refresh();
  if (restoreFocus) $(failure.showRetry ? "mic-retry" : "check-mic").focus();
  if (deviceDisconnected || ["NotReadableError", "NotFoundError"].includes(cause?.name) ||
      (cause?.name === "OverconstrainedError" && cause.constraint === "deviceId")) {
    prejoinAbort = new AbortController();
    void showMicDevices(prejoinAbort.signal);
  }
}

async function runMicCheck(control) {
  if (expireUnusedInvite() || !invite || checkRunning || starting || session) return;
  const followCheck = document.activeElement === control;
  resetMicCheck();
  checkRunning = true;
  $("mic-progress").textContent = "Allow microphone access in your browser.";
  if (followCheck) $("mic-progress").focus();
  refresh();
  $("mic-error").textContent = "";
  prejoinAbort = new AbortController();
  const { signal } = prejoinAbort;
  try {
    if (!context || context.state === "closed") {
      context = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
    }
    await context.resume();
    if (signal.aborted || expireUnusedInvite()) return;
    const capture = await acquireMusicStream(navigator.mediaDevices, $("mic-device").value);
    if (signal.aborted || expireUnusedInvite()) {
      capture.stream.getTracks().forEach((track) => track.stop());
      return;
    }
    prejoinStream = capture.stream;
    prejoinWarnings = capture.warnings;
    const track = prejoinStream.getAudioTracks()[0];
    if (!watchMicrophone(track, signal, () => {
      $("mic-device").value = "";
      showMicError(new Error("Microphone disconnected. Reconnect it or choose another input, then try again."), true);
    })) return;
    showAudioSettings(prejoinStream, prejoinWarnings);
    const source = $("mic-source");
    source.textContent = track.label ? `Using ${track.label}` : "Microphone connected.";
    source.hidden = false;
    void showMicDevices(signal);
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
        const continueCheck = document.activeElement === $("mic-progress");
        micHeard = true;
        $("mic-progress").textContent = "";
        $("mic-heard").hidden = false;
        refresh();
        if (continueCheck) focusNextAudioCheckControl();
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
$("check-mic").addEventListener("click", (event) => runMicCheck(event.currentTarget));
$("mic-retry").addEventListener("click", (event) => runMicCheck(event.currentTarget));
$("mic-device").addEventListener("change", (event) => {
  checkSkipped = false;
  void runMicCheck(event.currentTarget);
});
$("skip-check").addEventListener("click", (event) => {
  if (expireUnusedInvite() || !invite || session || checkSkipped) return;
  const continueCheck = document.activeElement === event.currentTarget;
  resetMicCheck();
  checkSkipped = true;
  $("mic-progress").textContent = "Mic check skipped. Your browser may ask for access when you join.";
  refresh();
  if (continueCheck) focusNextAudioCheckControl();
});

function setPlaybackRecovery(blocked) {
  $("play-audio").hidden = !blocked;
  $("leave").classList.toggle("secondary", blocked);
  $("playback-help").textContent = blocked
    ? "You’re still connected. Select Play session audio to hear your host."
    : "";
}

async function playSessionAudio(attempt) {
  const audio = $("host-audio");
  const isCurrent = () => !!attempt && activeAttempt === attempt && !attempt.signal.aborted;
  if (!isCurrent() || !audio.srcObject) return;
  try {
    await audio.play();
  } catch (error) {
    if (!isCurrent()) return;
    if (error?.name !== "NotAllowedError") {
      leave("Playback could not start.");
      return;
    }
    setPlaybackRecovery(true);
    if (document.activeElement === $("leave")) $("play-audio").focus();
    return;
  }
  if (!isCurrent()) return;
  const restoreFocus = document.activeElement === $("play-audio");
  setPlaybackRecovery(false);
  if (restoreFocus) $("leave").focus();
}

function leave(message = "You left the session.", error) {
  const elements = failureElements();
  const restoreFocus = [elements.leave, $("play-audio")].includes(document.activeElement);
  const joined = session?.authorized === true;
  clearAccessTime();
  activeAttempt?.abort();
  activeAttempt = null;
  session?.close();
  session = null;
  starting = false;
  $("host-audio").pause();
  $("host-audio").srcObject = null;
  setPlaybackRecovery(false);
  stopMicMonitoring(false);
  void closeAudioContext(context);
  context = null;
  $("leave").hidden = true;
  $("connection-route").hidden = true;
  $("latency").textContent = "Listening delay is estimated after you connect.";
  let view = error ? mapJoinError(error, { listenOnly: retryListening }) : null;
  if (joined && (!view || view.showRetry)) {
    const ended = mapJoinError(JOIN_SIGNAL.SESSION_ENDED);
    view = { ...ended, kind: view?.kind || ended.kind, headline: view?.headline || message };
  }
  if (view) {
    if (view.needsFreshInvite && !view.showRetry) invite = undefined;
    applyJoinFailure(view, elements);
  } else {
    clearJoinFailure(elements, message);
  }
  if (error) elements.status.dataset.failure = "true";
  else delete elements.status.dataset.failure;
  refresh();
  if (restoreFocus) {
    const nextAction = [elements.retry, retryListening && $("listen"), elements.join].filter(Boolean).find(
      (button) => !button.hidden && !button.disabled,
    );
    (nextAction || elements.status).focus();
  }
  scheduleUnusedInviteExpiry();
}

async function startJoin(listenOnly = false) {
  if (expireUnusedInvite() || !invite || starting || session || (!listenOnly && !joinReady())) return;
  const elements = failureElements();
  const activeControl = document.activeElement;
  retryListening = listenOnly;
  starting = true;
  if (listenOnly) stopMicMonitoring(true);
  refresh();
  delete elements.status.dataset.failure;
  clearJoinFailure(elements, "Connecting…");
  elements.leave.hidden = false;
  if (activeControl === $("listen") || activeControl === elements.join || activeControl === elements.retry) {
    elements.leave.focus();
  }
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
    if (!isCurrent() || expireUnusedInvite()) return;
    if (!listenOnly && prejoinStream?.getAudioTracks().some((track) => track.readyState === "live")) {
      stream = prejoinStream;
      prejoinStream = null;
      prejoinAbort?.abort();
      prejoinAbort = null;
      stopMeter?.();
      stopMeter = null;
      showAudioSettings(stream, prejoinWarnings);
    } else if (!listenOnly) {
      $("status").textContent = "Allow microphone access to play with your host.";
      const capture = await acquireMusicStream(navigator.mediaDevices, $("mic-device").value);
      stream = capture.stream;
      if (!isCurrent() || expireUnusedInvite()) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      showAudioSettings(stream, capture.warnings);
    }
    if (stream && !watchMicrophone(stream.getAudioTracks()[0], attempt.signal, () => {
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
      onRoute: (route) => {
        if (!isCurrent()) return;
        const indicator = $("connection-route");
        if (route) indicator.textContent = route === "relayed" ? "Relayed connection" : "Direct connection";
        indicator.hidden = route === null;
      },
      onTrack: (remote) => {
        if (!isCurrent()) return;
        $("host-audio").srcObject = remote;
        void playSessionAudio(attempt);
      },
      onError: (error) => {
        if (isCurrent()) leave(undefined, error);
      },
    });
    session = joinedSession;
    clearTimeout(inviteExpiryTimer);
    await joinedSession.join();
    if (!isCurrent()) return;
    showAccessTime(joinedSession.accessExpiresAt, isCurrent);
    if (listenOnly) $("status").textContent = "Connected. You’re listening with your microphone off.";
    clearJoinFailure(failureElements(), $("status").textContent);
    $("join").hidden = true;
    const resumeAudio = $("play-audio");
    (resumeAudio.hidden ? $("leave") : resumeAudio).focus({ preventScroll: resumeAudio.hidden });
    starting = false;
    refresh();
  } catch (error) {
    stream?.getTracks().forEach((track) => track.stop());
    if (isCurrent()) leave(undefined, error);
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
listen?.addEventListener("click", () => {
  void startJoin(true);
});
$("retry").addEventListener("click", () => {
  void startJoin(retryListening);
});
$("play-audio").addEventListener("click", () => { void playSessionAudio(activeAttempt); });
$("leave").addEventListener("click", () => leave());
window.addEventListener("hashchange", () => window.location.reload());
window.addEventListener("pagehide", () => leave());
document.addEventListener("visibilitychange", () => {
  if (document.hidden || expireUnusedInvite()) return;
  if (context?.state === "suspended") {
    const attempt = activeAttempt;
    context.resume().catch(() => {
      if (attempt && !attempt.signal.aborted && activeAttempt === attempt) {
        leave(session?.authorized ? "Browser audio was interrupted." : "Tap Join again to resume audio.");
      }
    });
  }
});
refresh();
scheduleUnusedInviteExpiry();
