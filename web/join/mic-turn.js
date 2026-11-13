/** Mic work that outlives Leave, rejoin, or a device change must not touch the new room. */

export const MIC_STILL_ON_MESSAGE = "The microphone is still on. You are still in the room.";

export function micTurnSuperseded(captured, now) {
  return now.session !== captured.session || now.generation !== captured.generation;
}

/**
 * Settle Back to listening for the room that started the release.
 * Returns "listen", "still-on", or "ignored". A later Leave, rejoin, or device
 * change makes the release "ignored": no status write and no mic teardown.
 */
export async function finishMicrophoneRelease(read) {
  const start = read();
  if (!start.micHeard || !start.session || start.checkRunning || start.starting) return "ignored";
  const captured = { session: start.session, generation: start.generation };
  let released = true;
  if (typeof captured.session.releaseMicrophone === "function") {
    try {
      released = await captured.session.releaseMicrophone() === true;
    } catch {
      released = false;
    }
  }
  const now = read();
  if (!now.session || micTurnSuperseded(captured, now)) return "ignored";
  if (!released) return "still-on";
  return "listen";
}
