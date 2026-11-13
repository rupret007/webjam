import test from "node:test";
import assert from "node:assert/strict";
import { FIXTURE_INVITE_URL, INVITE_SHARE_TITLE, inviteMailto, shareWithoutActivation } from "../share.js";

test("mailto keeps the example invite and does not add deliver", () => {
  const href = inviteMailto(FIXTURE_INVITE_URL, INVITE_SHARE_TITLE);
  const parsed = new URL(href);
  assert.equal(parsed.protocol, "mailto:");
  assert.equal(parsed.searchParams.get("subject"), INVITE_SHARE_TITLE);
  const body = parsed.searchParams.get("body");
  assert.equal(body, FIXTURE_INVITE_URL);
  assert.equal(body.startsWith("https://example.com/"), true);
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
