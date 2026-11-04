import assert from "node:assert/strict";
import test from "node:test";

import {
  CLIPBOARD_DENIED_NOTICE,
  COPIED_NOTICE,
  copyInviteText,
} from "../clipboard.js";

test("copyInviteText uses navigator.clipboard when available", async () => {
  let written = "";
  const result = await copyInviteText("https://example/join/#k=abc", {
    clipboard: {
      writeText(value) {
        written = value;
        return Promise.resolve();
      },
    },
  });
  assert.equal(written, "https://example/join/#k=abc");
  assert.equal(result.ok, true);
  assert.equal(result.usedFallback, false);
  assert.equal(result.notice, COPIED_NOTICE);
});

test("copyInviteText falls back when clipboard write is denied", async () => {
  const calls = [];
  const doc = {
    body: { appendChild(node) { calls.push(["append", node.value]); }, },
    createElement() {
      return {
        value: "",
        style: {},
        setAttribute() {},
        select() { calls.push(["select"]); },
        remove() { calls.push(["remove"]); },
      };
    },
    execCommand(name) {
      calls.push(["exec", name]);
      return false;
    },
  };
  const result = await copyInviteText("https://example/join/#secret", {
    clipboard: {
      writeText() {
        return Promise.reject(new Error("denied"));
      },
    },
    document: doc,
  });
  assert.equal(result.ok, false);
  assert.equal(result.usedFallback, true);
  assert.equal(result.notice, CLIPBOARD_DENIED_NOTICE);
  assert.deepEqual(calls[0], ["append", "https://example/join/#secret"]);
});
