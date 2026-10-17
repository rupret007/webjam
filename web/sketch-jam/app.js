import { RELAY_ORIGIN } from "./config.js";
import { createInvite, inviteFragment, readInvite } from "./crypto.js";
import { acquireMusicStream } from "./audio.js";
import { SketchRoom } from "./room.js";

const byId = (id) => document.getElementById(id);
let invite;
let room;
let audioContext;
let generation = 0;
let active = false;
let used = false;
const playback = new Map();

function status(message) { byId("status").textContent = message; }
function controls() {
  byId("join").disabled = active || used || !invite || !byId("headphones").checked;
  byId("leave").disabled = !active;
  byId("name").disabled = active;
  byId("headphones").disabled = active;
}

function loadInvite(fresh = false) {
  try {
    invite = !fresh && location.hash ? readInvite(location.hash) : createInvite();
    const url = new URL(location.href);
    url.hash = inviteFragment(invite);
    history.replaceState(null, "", url);
    byId("invite").value = url.href;
    byId("copy").disabled = false;
  } catch (error) {
    invite = null;
    byId("copy").disabled = true;
    status(error.message);
  }
  controls();
}

function showPeer({ index, name, state, latency }) {
  let entry = byId(`peer-${index}`);
  if (!entry) {
    entry = document.createElement("li");
    entry.id = `peer-${index}`;
    entry.dataset.peer = String(index);
    const label = document.createElement("strong");
    const detail = document.createElement("span");
    detail.className = "peer-detail";
    entry.append(label, detail);
    byId("peers").append(entry);
  }
  entry.dataset.state = state;
  entry.children[0].textContent = name;
  entry.children[1].textContent = state === "connected" ? `Connected · ${Number.isFinite(latency) ? `${Math.round(latency)} ms estimated` : "measuring latency…"}` : state;
}

async function playRemote(index, stream) {
  let audio = playback.get(index);
  if (!audio) {
    audio = document.createElement("audio");
    audio.autoplay = true;
    audio.playsInline = true;
    playback.set(index, audio);
    document.body.append(audio);
  }
  audio.srcObject = stream;
  try { await audio.play(); } catch {
    if (active && playback.get(index) === audio) byId("resume").hidden = false;
  }
}

function stop() {
  generation++;
  room?.close();
  room = null;
  active = false;
  for (const audio of playback.values()) { audio.pause(); audio.srcObject = null; audio.remove(); }
  playback.clear();
  audioContext?.close().catch(() => {});
  audioContext = null;
  byId("peers").replaceChildren();
  byId("resume").hidden = true;
  controls();
}

async function join() {
  if (active || used || !invite || !byId("headphones").checked) return;
  const turn = ++generation;
  active = true;
  controls();
  status("Starting music audio…");
  let stream;
  try {
    // Construct and resume inside the tap handler for Safari's audio activation.
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!Audio || !navigator.mediaDevices?.getUserMedia || !window.RTCPeerConnection) throw new Error("This browser cannot start Sketch Jam audio. Try Safari, Chrome, or Firefox over HTTPS.");
    try { audioContext = new Audio({ latencyHint: "interactive", sampleRate: 48000 }); } catch { audioContext = new Audio({ latencyHint: "interactive" }); }
    await audioContext.resume();
    if (turn !== generation) return;
    const acquired = await acquireMusicStream(navigator.mediaDevices);
    stream = acquired.stream;
    if (turn !== generation) { stream.getTracks().forEach((track) => track.stop()); return; }
    byId("audio-warnings").textContent = acquired.warnings.length ? `Your browser changed or could not confirm music settings: ${acquired.warnings.join("; ")}.` : "";
    byId("audio-warnings").hidden = acquired.warnings.length === 0;
    invite = readInvite(location.hash);
    room = new SketchRoom({ origin: RELAY_ORIGIN, invite, stream, audioContext, onStatus: status, onPeer: showPeer, onTrack: playRemote, onError: (error) => { stop(); status(error.message || "Unable to join this Sketch Jam."); } });
    status("Joining with encrypted signaling…");
    await room.join(byId("name").value);
    used = true;
  } catch (error) {
    stream?.getTracks().forEach((track) => track.stop());
    if (turn !== generation) return;
    stop();
    status(error.name === "NotAllowedError"
      ? "Microphone access is needed. Allow it, then tap Join Sketch Jam again."
      : error.message || "Unable to join this Sketch Jam.");
  }
}

byId("headphones").addEventListener("change", controls);
byId("join").addEventListener("click", join);
byId("leave").addEventListener("click", () => { stop(); status("You left the jam. Create a new link to start again."); });
byId("new-room").addEventListener("click", () => {
  stop();
  used = false;
  byId("peers").replaceChildren();
  byId("audio-warnings").hidden = true;
  byId("headphones").checked = false;
  status("New invite ready. Check your headphones, then tap Join to start audio.");
  loadInvite(true);
});
byId("copy").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(byId("invite").value); status("Invite copied. Share it only with the people you want to join."); }
  catch { byId("invite").select(); status("Select and copy the private invite link above."); }
});
byId("resume").addEventListener("click", async () => {
  try { await audioContext?.resume(); await Promise.all([...playback.values()].map((audio) => audio.play())); byId("resume").hidden = true; }
  catch { status("Audio is paused. Keep this tab visible, then tap Resume audio again."); }
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && active) audioContext?.resume().catch(() => { byId("resume").hidden = false; });
});
window.addEventListener("pagehide", stop);
window.addEventListener("hashchange", () => {
  if (active) stop();
  used = false;
  loadInvite();
});
loadInvite();
