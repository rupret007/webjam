import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { HEADPHONES_NOTE, isRoomTitle, joiningLine, listeningLine, micOnLine } from "../listening.js";

const sessionSource = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "../session.js"),
  "utf8",
);
const appSource = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "../app.js"),
  "utf8",
);
const pageSource = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "../index.html"),
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
  assert.equal(appSource.includes('$("leave").focus'), false);
  assert.equal(appSource.includes("You are listening"), false);
  assert.equal(appSource.includes("Listening only"), false);
  assert.match(appSource, /returnToListening/);
});

test("joining and mic-on titles stay one plain line, with a session name when it is already known", () => {
  assert.equal(joiningLine(), "Joining the room…");
  assert.equal(joiningLine("Friday set"), "Joining Friday set…");
  assert.equal(joiningLine("https://example.com/room"), "Joining the room…");
  assert.equal(micOnLine(), "You're in, mic on");
  assert.equal(micOnLine("  Friday set  "), "You're in, Friday set. Mic on");
  assert.equal(micOnLine("guest@example.com"), "You're in, mic on");
  assert.equal(isRoomTitle("You're listening."), true);
  assert.equal(isRoomTitle("You're in, mic on"), true);
  assert.equal(isRoomTitle("Checking the host invitation…"), false);
  assert.equal(HEADPHONES_NOTE.includes("listening"), false);
  assert.match(pageSource, /Joining the room…/);
  assert.match(pageSource, /id="leave"[^>]*class="secondary quiet"/);
  assert.match(pageSource, /id="listen-only"/);
  assert.equal(pageSource.includes('id="headphones"'), false);
  assert.equal(pageSource.includes("You are listening"), false);
  assert.equal(pageSource.includes("<h2"), false);
});
