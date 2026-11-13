import test from "node:test";
import assert from "node:assert/strict";
import { FIXTURE_INVITE_URL, INVITE_SHARE_TEXT, INVITE_SHARE_TITLE, inviteBody, inviteMailto, shareWithoutActivation } from "../share.js";

test("mailto keeps the example invite and tells the guest to tap to listen", () => {
  const href = inviteMailto(FIXTURE_INVITE_URL, INVITE_SHARE_TITLE);
  const parsed = new URL(href);
  assert.equal(parsed.protocol, "mailto:");
  assert.equal(parsed.searchParams.get("subject"), INVITE_SHARE_TITLE);
  const body = parsed.searchParams.get("body");
  assert.equal(body, inviteBody(FIXTURE_INVITE_URL));
  assert.equal(body.split("\n")[0], INVITE_SHARE_TEXT);
  assert.equal(INVITE_SHARE_TEXT, "Tap to listen, no app, no microphone.");
  assert.equal(body.split("\n")[1], FIXTURE_INVITE_URL);
  assert.equal(body.includes("https://example.com/"), true);
  assert.equal(body.includes("#") && body.split("#")[1].length > 0, true);
  assert.equal(body.includes("deliver"), false);
});

test("a share helper without activation rejects and does not call share", () => {
  let calls = 0;
  const share = () => {
    calls += 1;
    return Promise.resolve();
  };
  assert.throws(() => shareWithoutActivation({ isActive: false }, { type: "click" }), { name: "NotAllowedError" });
  assert.throws(() => shareWithoutActivation({ isActive: true }, null), { name: "NotAllowedError" });
  assert.throws(() => shareWithoutActivation(undefined), { name: "NotAllowedError" });
  assert.equal(calls, 0);
  assert.doesNotThrow(() => shareWithoutActivation({ isActive: true }, { type: "click" }));
  assert.equal(typeof share, "function");
});
