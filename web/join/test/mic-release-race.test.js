import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { listeningLine } from "../listening.js";
import {
  MIC_STILL_ON_MESSAGE,
  finishMicrophoneRelease,
  micTurnSuperseded,
} from "../mic-turn.js";

const appSource = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "../app.js"),
  "utf8",
);

function deferred() {
  let settle;
  const promise = new Promise((resolve) => {
    settle = resolve;
  });
  return { promise, settle };
}

function pageFor(session, generation = 0) {
  const page = {
    session,
    generation,
    micHeard: true,
    checkRunning: false,
    starting: false,
    micError: "",
    status: "You're in, mic on",
    title: "You're in, mic on",
    monitor: { id: "current", stopped: false },
    stops: [],
    stopMicMonitoring(clearHeard) {
      page.stops.push({
        session: page.session,
        generation: page.generation,
        monitor: page.monitor,
        clearHeard,
      });
      page.monitor.stopped = true;
      if (clearHeard) page.micHeard = false;
    },
  };
  return page;
}

/** Same branch the page uses after finishMicrophoneRelease. */
function applyRelease(outcome, page) {
  if (outcome !== "still-on" && outcome !== "listen") return;
  if (outcome === "still-on") {
    page.micError = MIC_STILL_ON_MESSAGE;
    return;
  }
  page.stopMicMonitoring(true);
  page.title = listeningLine(page.session?.sessionName);
  page.micError = "";
}

function pendingRelease(page) {
  const gate = deferred();
  let calls = 0;
  page.session.releaseMicrophone = () => {
    calls += 1;
    return gate.promise;
  };
  const finished = finishMicrophoneRelease(() => page);
  return {
    calls: () => calls,
    finish: gate.settle,
    finished,
  };
}

test("Leave during a pending mic release writes no stale status and does not stop the new state", async () => {
  for (const released of [true, false]) {
    const room = { id: "old" };
    const page = pageFor(room, 4);
    const pending = pendingRelease(page);
    page.generation += 1;
    page.session = null;
    page.status = "You left the session.";
    page.micError = "";
    page.monitor = { id: "after-leave", stopped: false };
    pending.finish(released);
    const outcome = await pending.finished;
    applyRelease(outcome, page);
    assert.equal(outcome, "ignored");
    assert.equal(page.micError, "");
    assert.equal(page.status, "You left the session.");
    assert.equal(page.monitor.stopped, false);
    assert.deepEqual(page.stops, []);
    assert.equal(pending.calls(), 1);
  }
});

test("rejoin during a pending mic release leaves the new session untouched", async () => {
  for (const released of [true, false]) {
    const room = { id: "old" };
    const page = pageFor(room, 2);
    const pending = pendingRelease(page);
    const next = Object.freeze({
      id: "new",
      listenOnly: true,
      stream: null,
      releaseMicrophone() {
        throw new Error("new session must stay untouched");
      },
    });
    page.generation += 1;
    page.session = null;
    page.generation += 1;
    page.session = next;
    page.status = "You're listening. Friday set";
    page.micError = "";
    page.monitor = { id: "rejoin", stopped: false };
    pending.finish(released);
    const outcome = await pending.finished;
    applyRelease(outcome, page);
    assert.equal(outcome, "ignored");
    assert.equal(page.session, next);
    assert.equal(next.listenOnly, true);
    assert.equal(next.stream, null);
    assert.equal(page.micError, "");
    assert.equal(page.status, "You're listening. Friday set");
    assert.equal(page.monitor.stopped, false);
    assert.deepEqual(page.stops, []);
    assert.equal(pending.calls(), 1);
  }
});

test("a device change during a pending mic release does not stop the new monitor or rewrite status", async () => {
  for (const released of [true, false]) {
    const room = { id: "same" };
    const page = pageFor(room, 7);
    const pending = pendingRelease(page);
    page.generation += 1;
    page.monitor = { id: "new-device", stopped: false };
    page.micError = "";
    page.status = "You're in, mic on";
    pending.finish(released);
    const outcome = await pending.finished;
    applyRelease(outcome, page);
    assert.equal(outcome, "ignored");
    assert.equal(page.session, room);
    assert.equal(page.monitor.stopped, false);
    assert.equal(page.micError, "");
    assert.equal(page.status, "You're in, mic on");
    assert.deepEqual(page.stops, []);
  }
});

test("a finished mic release still returns to listening", async () => {
  const room = { id: "old", sessionName: "" };
  const page = pageFor(room, 1);
  const pending = pendingRelease(page);
  pending.finish(true);
  const outcome = await pending.finished;
  applyRelease(outcome, page);
  assert.equal(outcome, "listen");
  assert.equal(page.title, "You're listening.");
  assert.equal(page.micHeard, false);
  assert.equal(page.micError, "");
  assert.equal(page.monitor.stopped, true);
  assert.deepEqual(page.stops, [{
    session: room,
    generation: 1,
    monitor: page.monitor,
    clearHeard: true,
  }]);
});

test("a rejected mic release still reports that the microphone is on when the room is unchanged", async () => {
  const room = { id: "old" };
  const page = pageFor(room, 1);
  const pending = pendingRelease(page);
  pending.finish(false);
  const outcome = await pending.finished;
  applyRelease(outcome, page);
  assert.equal(outcome, "still-on");
  assert.equal(page.micError, "The microphone is still on. You are still in the room.");
  assert.equal(page.micHeard, true);
  assert.equal(page.monitor.stopped, false);
  assert.deepEqual(page.stops, []);

  const throwing = pageFor({ id: "throws" }, 1);
  let fail;
  const promise = new Promise((_, reject) => {
    fail = reject;
  });
  throwing.session.releaseMicrophone = () => promise;
  const finished = finishMicrophoneRelease(() => throwing);
  fail(new Error("replaceTrack failed"));
  assert.equal(await finished, "still-on");
});

test("a later session or join generation supersedes an in-flight mic turn", () => {
  const room = { id: "room" };
  const captured = { session: room, generation: 3 };
  assert.equal(micTurnSuperseded(captured, { session: room, generation: 3 }), false);
  assert.equal(micTurnSuperseded(captured, { session: room, generation: 4 }), true);
  assert.equal(micTurnSuperseded(captured, { session: { id: "other" }, generation: 3 }), true);
  assert.equal(micTurnSuperseded(captured, { session: null, generation: 3 }), true);
});

test("the page bumps the join generation for Leave, rejoin, and device changes before settling the mic", () => {
  const release = appSource.slice(
    appSource.indexOf("async function returnToListening"),
    appSource.indexOf("function leave("),
  );
  const decide = release.indexOf('outcome !== "still-on" && outcome !== "listen"');
  const write = release.indexOf("MIC_STILL_ON_MESSAGE");
  const stop = release.indexOf("stopMicMonitoring(true)");
  assert.match(release, /finishMicrophoneRelease\(\(\) => micNow\(\)\)/);
  assert.ok(decide !== -1 && decide < write && write < stop);

  const leave = appSource.slice(appSource.indexOf("function leave("), appSource.indexOf("async function startJoin"));
  assert.ok(leave.indexOf("bumpJoinGeneration()") !== -1);
  assert.ok(leave.indexOf("bumpJoinGeneration()") < leave.indexOf("session = null"));

  assert.match(appSource, /session = joinedSession;\n    bumpJoinGeneration\(\);/);

  const check = appSource.slice(
    appSource.indexOf("async function runMicCheck"),
    appSource.indexOf('$("check-mic").addEventListener'),
  );
  const guard = check.indexOf("checkRunning || starting) return;");
  const bump = check.indexOf("bumpJoinGeneration()");
  assert.ok(guard !== -1 && bump > guard);

  const publish = appSource.slice(
    appSource.indexOf("async function publishMicrophone"),
    appSource.indexOf("async function runMicCheck"),
  );
  assert.ok(publish.indexOf("micTurnSuperseded(turn, micNow())") < publish.indexOf("showMicError"));
  assert.match(check, /micTurnSuperseded\(turn, micNow\(\)\)/);
  assert.equal(appSource.includes("await session.releaseMicrophone"), false);
});
