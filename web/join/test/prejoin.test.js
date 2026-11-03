import test from "node:test";
import assert from "node:assert/strict";
import {
  MIC_HEARD_THRESHOLD,
  micCheckFailure,
  hasAudioInputDevice,
  micLevelRms,
  beginMicCheck,
} from "../prejoin.js";

test("mic check errors map to plain recovery text", () => {
  assert.match(micCheckFailure({ name: "NotFoundError" }).message, /No microphone found/);
  assert.equal(micCheckFailure({ name: "NotFoundError" }).showRetry, true);
  assert.match(micCheckFailure({ name: "NotAllowedError" }).message, /blocked/i);
  assert.equal(micCheckFailure({ name: "NotAllowedError" }).showRetry, true);
});

test("audio input detection and RMS threshold", () => {
  assert.equal(hasAudioInputDevice([{ kind: "videoinput" }]), false);
  assert.equal(hasAudioInputDevice([{ kind: "audioinput", label: "Mic" }]), true);
  const analyser = {
    fftSize: 4,
    getFloatTimeDomainData(buffer) {
      buffer[0] = 0.1;
      buffer[1] = 0;
      buffer[2] = 0;
      buffer[3] = 0;
    },
  };
  assert.ok(micLevelRms(analyser) >= MIC_HEARD_THRESHOLD);
});

test("beginMicCheck fails closed when no audio inputs are listed", async () => {
  await assert.rejects(
    beginMicCheck({
      enumerateDevices: async () => [{ kind: "audiooutput" }],
      getUserMedia: async () => {
        throw new DOMException("Requested device not found", "NotFoundError");
      },
    }),
    { name: "NotFoundError" },
  );
});
