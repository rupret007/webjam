import { readInvite } from "./crypto.js";
import { acquireMusicStream } from "./audio.js";
import { WebJoinSession } from "./session.js";
import { RELAY_ORIGIN } from "./config.js";
const $ = (id) => document.getElementById(id);
let invite, session, context, starting = false, activeAttempt;
try { invite = readInvite(location.hash); $("status").textContent = "Your host invited you. Put on headphones, then join."; }
catch (error) { $("status").textContent = error.message; }
const refresh = () => { $("join").disabled = !invite || !$("headphones").checked || starting || !!session; };
$("headphones").addEventListener("change", refresh);
function leave(message = "You left the session.") {
  activeAttempt?.abort(); activeAttempt = null;
  session?.close(); session = null; starting = false;
  $("host-audio").pause(); $("host-audio").srcObject = null;
  context?.close().catch(() => {}); context = null;
  $("leave").hidden = true; $("status").textContent = message; refresh();
}
$("join").addEventListener("click", async () => {
  if (!invite || !$("headphones").checked || starting || session) return;
  starting = true; refresh(); $("leave").hidden = false;
  const attempt = activeAttempt = new AbortController();
  let stream;
  const cancel = () => attempt.abort();
  $("leave").addEventListener("click", cancel, { once: true });
  try {
    // Must start in the user gesture on iOS; capture permission can await later.
    context = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
    await context.resume();
    if (attempt.signal.aborted) return;
    $("status").textContent = "Allow microphone access to play with your host.";
    const capture = await acquireMusicStream(navigator.mediaDevices);
    stream = capture.stream;
    if (attempt.signal.aborted) { stream.getTracks().forEach((track) => track.stop()); return; }
    $("warnings").textContent = capture.warnings.join("\n");
    session = new WebJoinSession({ origin: RELAY_ORIGIN, invite, stream, audioContext: context,
      onStatus: (message) => { $("status").textContent = message; },
      onLatency: (milliseconds) => { $("latency").textContent = milliseconds === null ? "Estimated listening delay: unavailable in this browser." : `Estimated listening delay: ${Math.round(milliseconds)} ms (network + playback; excludes capture and host bridge).`; },
      onTrack: (remote) => {
        if (attempt.signal.aborted || activeAttempt !== attempt) return;
        $("host-audio").srcObject = remote;
        $("host-audio").play().catch(() => {
          // pause() or a later Join may settle an earlier playback promise.
          if (!attempt.signal.aborted && activeAttempt === attempt) leave("Playback could not start. Tap Join again.");
        });
      },
      onError: (error) => { if (!attempt.signal.aborted) leave(error.message); },
    });
    await session.join();
    starting = false; refresh();
  } catch (error) {
    stream?.getTracks().forEach((track) => track.stop());
    if (!attempt.signal.aborted) leave(error.name === "AbortError"
      ? "Joining timed out or was interrupted. Check your connection and microphone, then try Join again."
      : error.message);
  } finally { $("leave").removeEventListener("click", cancel); }
});
$("leave").addEventListener("click", () => leave());
window.addEventListener("pagehide", () => leave());
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && context?.state === "suspended") {
    const attempt = activeAttempt;
    context.resume().catch(() => {
      if (attempt && !attempt.signal.aborted && activeAttempt === attempt) leave("Tap Join again to resume audio.");
    });
  }
});
refresh();
