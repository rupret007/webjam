import test from "node:test";
import assert from "node:assert/strict";
import {
  MIC_HEARD_THRESHOLD,
  micCheckFailure,
  micLevelRms,
} from "../prejoin.js";

test("mic check errors map to plain recovery text", () => {
  assert.match(micCheckFailure({ name: "NotFoundError" }).message, /No microphone found/);
  assert.equal(micCheckFailure({ name: "NotFoundError" }).showRetry, true);
  assert.match(micCheckFailure({ name: "NotAllowedError" }).message, /blocked/i);
  assert.equal(micCheckFailure({ name: "NotAllowedError" }).showRetry, true);
});

test("audio level exceeds the RMS detection threshold", () => {
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
