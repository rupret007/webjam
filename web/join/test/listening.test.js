import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { listeningLine } from "../listening.js";

const sessionSource = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "../session.js"),
  "utf8",
);
const appSource = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "../app.js"),
  "utf8",
);

test("the in-room line is You're listening, plus a plain session name when one is known", () => {
  assert.equal(listeningLine(), "You're listening.");
  assert.equal(listeningLine(""), "You're listening.");
  assert.equal(listeningLine("   "), "You're listening.");
  assert.equal(listeningLine("Friday set"), "You're listening. Friday set");
  assert.equal(listeningLine("  Friday set  "), "You're listening. Friday set");
  assert.equal(listeningLine("https://example.com/room"), "You're listening.");
  assert.equal(listeningLine("room/name"), "You're listening.");
  assert.equal(listeningLine("guest@example.com"), "You're listening.");
  assert.equal(listeningLine("a".repeat(41)), "You're listening.");
  assert.equal(listeningLine("11".repeat(16)), "You're listening.");
  assert.match(sessionSource, /onStatus\(listeningLine\(this\.sessionName\)\)/);
  assert.equal(sessionSource.includes("higher delay (about 60–120 ms)"), false);
  assert.match(appSource, /if \(invite\) void startJoin\(\)/);
  assert.equal(appSource.includes("Speak or play a note"), false);
});
