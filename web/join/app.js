import { readInvite } from "./crypto.js";
import { closeAudioContext } from "./audio.js";
import { WebJoinSession } from "./session.js";
import { HEADPHONES_NOTE, isRoomTitle, joiningLine, listeningLine, micOnLine } from "./listening.js";
import { RELAY_ORIGIN } from "./config.js";
import {
  beginMicCheck,
  micCheckFailure,
  startMicLevelMonitor,
} from "./prejoin.js";
import { JOIN_SIGNAL, applyJoinFailure, clearJoinFailure, mapJoinError } from "./errors.js";

const $ = (id) => document.getElementById(id);
const PAGE_HEADING = "Join a WebJam session";
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
let checkRunning = false;
let prejoinAbort;
let accessTimer = null;
let inviteExpiryTimer = null;

function expireUnusedInvite() {
  if (!invite || session || Date.now() < invite.expiresAt * 1000) return false;
  leave(undefined, new Error(JOIN_SIGNAL.INVITE_EXPIRED));
  $("status").focus({ preventScroll: true });
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
  view.hint = "Ask your host for a new browser invite and open the full link.";
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
  $("check-mic").disabled = micHeard;
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
  return Boolean(invite) && !starting && !session;
}

function roomTitle(listening) {
  if (listening && micHeard) return micOnLine(session?.sessionName);
  if (listening) return listeningLine(session?.sessionName);
  if (starting) return joiningLine(session?.sessionName);
  return PAGE_HEADING;
}

const refresh = () => {
  const listening = Boolean(session) && !starting;
  const title = roomTitle(listening);
  const room = document.querySelector("main");
  if (room) room.classList.toggle("in-room", listening || starting);
  const dot = $("live-dot");
  if (dot) dot.hidden = !(listening || starting);
  $("prejoin").hidden = !listening;
  $("join").disabled = !joinReady();
  const heading = $("heading");
  if (heading) heading.textContent = title;
  const status = $("status");
  if (status && listening && isRoomTitle(status.textContent) && status.textContent !== title) {
    status.textContent = title;
  }
  if (status) status.classList.toggle("echo", listening && status.textContent === title);
  const note = $("listen-note");
  if (note) {
    note.hidden = !listening || micHeard;
    note.textContent = HEADPHONES_NOTE;
  }
  const back = $("listen-only");
  if (back) back.hidden = !listening || !micHeard;
  const guidance = $("join-guidance");
  if (guidance) {
    const message = starting
      ? "Connecting to your session…"
      : listening
        ? ""
        : invite
          ? "Join to listen. You do not need a microphone or headphones yet."
          : "";
    guidance.hidden = !message;
    if (guidance.textContent !== message) guidance.textContent = message;
  }
  const meter = $("mic-meter");
  if (meter) meter.hidden = !listening || (!checkRunning && !micHeard && !stopMeter);
  $("mic-device").disabled = !listening || checkRunning;
  const useMic = $("check-mic");
  useMic.hidden = Boolean(micHeard);
  useMic.disabled = !listening || checkRunning || micHeard || !!stopMeter;
  const actions = document.querySelector("main .actions");
  if (actions) actions.hidden = $("join").hidden && $("retry").hidden;
};

function showMicError(error) {
  stopMicMonitoring(true);
  // A failed optional check must not tear down audio the room is already using.
  if (!session) {
    void closeAudioContext(context);
    context = null;
  }
  const failure = micCheckFailure(error?.cause ?? error);
  $("mic-progress").textContent = "";
  $("mic-error").textContent = failure.message;
  $("mic-retry").hidden = !failure.showRetry;
  $("check-mic").disabled = false;
  checkRunning = false;
  refresh();
}

async function publishMicrophone(signal) {
  const track = prejoinStream?.getAudioTracks()[0];
  if (!track || signal.aborted || !session) return;
  $("mic-progress").textContent = "Turning the microphone on…";
  let on = false;
  try {
    if (typeof session.attachMicrophone === "function") on = await session.attachMicrophone(prejoinStream) === true;
  } catch {
    on = false;
  }
  if (signal.aborted) return;
  if (!on) {
    showMicError(new Error("The room cannot hear this microphone yet. You are still listening."));
    return;
  }
  micHeard = true;
  $("mic-progress").textContent = "";
  $("mic-error").textContent = "";
  $("mic-heard").hidden = false;
  refresh();
}

async function runMicCheck() {
  if (expireUnusedInvite() || !invite || !session || checkRunning || starting) return;
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
    if (signal.aborted || expireUnusedInvite()) return;
    $("mic-progress").textContent = "Allow microphone access in your browser.";
    const capture = await beginMicCheck(navigator.mediaDevices, $("mic-device").value);
    if (signal.aborted || expireUnusedInvite()) {
      capture.stream.getTracks().forEach((track) => track.stop());
      return;
    }
    prejoinStream = capture.stream;
    prejoinWarnings = capture.warnings;
    const track = prejoinStream.getAudioTracks()[0];
    if (!watchMicrophone(track, signal, () => {
      if (micHeard && session) {
        leave(undefined, new Error(JOIN_SIGNAL.MICROPHONE_ENDED));
        return;
      }
      stopMicMonitoring(true);
      $("mic-device").value = "";
      showMicError(new Error("Microphone disconnected. Reconnect it or choose another input, then try again."));
    })) return;
    showAudioSettings(prejoinStream, prejoinWarnings);
    void showMicDevices(track, signal);
    // The browser already allowed the microphone. Do not wait for a loud note.
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
      onHeard: () => {},
    });
    checkRunning = false;
    $("check-mic").disabled = true;
    refresh();
    await publishMicrophone(signal);
  } catch (error) {
    if (!signal.aborted) showMicError(error);
  }
}

$("check-mic").addEventListener("click", () => runMicCheck());
$("listen-only").addEventListener("click", () => {
  void returnToListening();
});
$("mic-retry").addEventListener("click", () => runMicCheck());
$("mic-device").addEventListener("change", () => {
  void runMicCheck();
});

async function returnToListening() {
  if (!micHeard || !session || checkRunning || starting) return;
  let released = true;
  if (typeof session.releaseMicrophone === "function") {
    try {
      released = await session.releaseMicrophone() === true;
    } catch {
      released = false;
    }
  }
  if (!released || !session) {
    $("mic-error").textContent = "The microphone is still on. You are still in the room.";
    refresh();
    return;
  }
  stopMicMonitoring(true);
  refresh();
}

function leave(message = "You left the session.", error) {
  prejoinAbort?.abort();
  prejoinAbort = null;
  clearAccessTime();
  activeAttempt?.abort();
  activeAttempt = null;
  session?.close();
  session = null;
  starting = false;
  $("host-audio").pause();
  $("host-audio").srcObject = null;
  stopMicMonitoring(false);
  void closeAudioContext(context);
  context = null;
  $("leave").hidden = true;
  $("connection-route").hidden = true;
  $("latency").textContent = "Listening delay is estimated after you connect.";
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
  scheduleUnusedInviteExpiry();
}

async function startJoin() {
  if (expireUnusedInvite() || !joinReady()) return;
  starting = true;
  refresh();
  delete $("status").dataset.failure;
  clearJoinFailure(failureElements(), "Connecting…");
  $("join").hidden = true;
  $("leave").hidden = false;
  refresh();
  const attempt = (activeAttempt = new AbortController());
  const isCurrent = () => activeAttempt === attempt && !attempt.signal.aborted;
  const cancel = () => attempt.abort();
  $("leave").addEventListener("click", cancel, { once: true });
  try {
    if (!context || context.state === "closed") {
      context = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
    }
    // Opening the invite is the join. Playback unlock can wait; the room cannot.
    context.resume().catch(() => {
      if (isCurrent()) leave("Tap Join again to resume audio.");
    });
    if (!isCurrent() || expireUnusedInvite()) return;
    const joinedSession = new WebJoinSession({
      origin: RELAY_ORIGIN,
      invite,
      stream: null,
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
    clearTimeout(inviteExpiryTimer);
    await joinedSession.join();
    if (!isCurrent()) return;
    showAccessTime(joinedSession.accessExpiresAt, isCurrent);
    clearJoinFailure(failureElements(), $("status").textContent);
    $("join").hidden = true;
    starting = false;
    refresh();
  } catch (error) {
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
  if (document.hidden || expireUnusedInvite()) return;
  if (context?.state === "suspended") {
    const attempt = activeAttempt;
    context.resume().catch(() => {
      if (attempt && !attempt.signal.aborted && activeAttempt === attempt) {
        leave("Tap Join again to resume audio.");
      }
    });
  }
});
scheduleUnusedInviteExpiry();
if (invite) void startJoin();
else refresh();
