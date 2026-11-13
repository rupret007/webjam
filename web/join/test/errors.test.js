import test from "node:test";
import assert from "node:assert/strict";
import { JOIN_SIGNAL, mapJoinError, applyJoinFailure, clearJoinFailure } from "../errors.js";

test("daily reset copy stays non-retrying and malformed hints retain generic cap recovery", () => {
  for (const seconds of [1, 86400, 0, 86401, -1, 1.5, "60", Infinity, null]) {
    const error = new Error(JOIN_SIGNAL.RELAY_CAP_REACHED);
    error.retryAfterSeconds = seconds;
    const view = mapJoinError(error);
    assert.equal(view.kind, "relay_cap");
    assert.equal(view.showRetry, false);
    assert.equal(view.needsFreshInvite, true);
    if (seconds === 1 || seconds === 86400) assert.match(view.hint, /daily request allowance resets around/);
    else assert.doesNotMatch(view.hint, /daily request allowance/);
  }
});

function controls() {
  const status = { textContent: "", dataset: {}, hidden: false };
  const hint = { textContent: "", hidden: true };
  const retry = { hidden: true, disabled: true, clicks: 0, addEventListener(_event, handler) { this._handler = handler; } };
  const join = { hidden: false, disabled: true };
  const leave = { hidden: false };
  return { status, hint, retry, join, leave, elements: { status, hint, retry, join, leave } };
}

test("an ended connection requires a fresh invite without blaming the host", () => {
  const view = mapJoinError(new Error(JOIN_SIGNAL.SESSION_ENDED));
  const ui = controls();
  applyJoinFailure(view, ui.elements);
  assert.equal(view.kind, "session_ended");
  assert.equal(view.needsFreshInvite, true);
  assert.equal(ui.status.textContent, "The Web Join connection ended.");
  assert.equal(ui.hint.textContent, "Ask your host to send a new browser invite. If you joined from Hang Out, return to your Jam room to accept it.");
  assert.equal(ui.retry.hidden, true);
  assert.equal(ui.join.hidden, true);
  assert.equal(ui.leave.hidden, true);
});

test("the used-invite mailbox signal asks for a fresh link", () => {
  const view = mapJoinError(new Error(JOIN_SIGNAL.ROOM_FULL));
  assert.equal(view.kind, "room_full");
  assert.equal(view.showRetry, false);
  assert.match(view.hint, /fresh Web Join link/i);
  const ui = controls();
  applyJoinFailure(view, ui.elements);
  assert.equal(ui.retry.hidden, true);
  assert.equal(ui.join.hidden, true);
  assert.equal(ui.retry.disabled, true);
});

test("host identity mismatch retires the old invitation and explains the next step", () => {
  const view = mapJoinError(new Error(JOIN_SIGNAL.HOST_BINDING));
  const ui = controls();
  applyJoinFailure(view, ui.elements);
  assert.equal(view.kind, "host_binding");
  assert.equal(view.needsFreshInvite, true);
  assert.equal(ui.status.textContent, "WebJam couldn't verify the host for this invite.");
  assert.match(ui.hint.textContent, /new browser invite.*return to your Jam room/);
  assert.equal(ui.retry.hidden, true);
  assert.equal(ui.retry.disabled, true);
  assert.equal(ui.join.hidden, true);
  assert.equal(ui.leave.hidden, true);
});

test("relay setup errors request host help instead of offering Retry", () => {
  for (const message of [
    "Untrusted relay configuration",
    "Untrusted relay address",
    "Invalid relay configuration",
    "Invalid relay credential",
  ]) {
    const view = mapJoinError(new Error(message));
    const ui = controls();
    applyJoinFailure(view, ui.elements);
    assert.equal(view.kind, "relay_configuration");
    assert.equal(view.needsFreshInvite, true);
    assert.equal(ui.status.textContent, "WebJam couldn't verify its browser audio relay.");
    assert.match(ui.hint.textContent, /Tell your host.*new browser invite/);
    assert.equal(ui.retry.hidden, true);
    assert.equal(ui.join.hidden, true);
  }
});

test("host offline maps from no-answer and authentication timeout signals with Retry", () => {
  for (const message of [JOIN_SIGNAL.HOST_NO_ANSWER, JOIN_SIGNAL.HOST_AUTH_TIMEOUT]) {
    const view = mapJoinError(new Error(message));
    assert.equal(view.kind, "host_offline");
    assert.equal(view.showRetry, true);
    assert.match(view.hint, /ask your host to keep the Music session open/i);
    assert.match(view.hint, /select Retry/i);
    assert.doesNotMatch(view.hint, /cop(?:y|ied)|fresh invite/i);
  }
});

test("network blocked maps direct and relay failure signals with Retry", () => {
  const gather = mapJoinError(new Error(JOIN_SIGNAL.ICE_GATHER_TIMEOUT));
  assert.equal(gather.kind, "network_blocked");
  assert.match(gather.hint, /relay/i);
  const relayFailed = mapJoinError(new Error(JOIN_SIGNAL.ICE_CONNECTION_FAILED));
  assert.equal(relayFailed.kind, "network_blocked");
  assert.match(relayFailed.headline, /relay/i);
  assert.match(relayFailed.hint, /hotspot/i);
});

test("an expired invite needs a new link and an expired relay session can retry", () => {
  const invite = mapJoinError(new Error(JOIN_SIGNAL.INVITE_EXPIRED));
  assert.equal(invite.kind, "invite_expired");
  assert.equal(invite.needsFreshInvite, true);
  assert.equal(invite.showRetry, false);
  assert.match(invite.hint, /new Web Join link/i);
  const turn = mapJoinError(new Error(JOIN_SIGNAL.RELAY_SESSION_EXPIRED));
  assert.equal(turn.kind, "turn_expired");
  assert.match(turn.hint, /fresh relay credentials/i);
  assert.equal(turn.showRetry, true);
});

test("relay outage keeps the existing unavailable wording and wires Retry visible", () => {
  const view = mapJoinError(new Error(JOIN_SIGNAL.RELAY_UNAVAILABLE));
  assert.match(view.headline, /Web Join is unavailable/);
  const ui = controls();
  applyJoinFailure(view, ui.elements);
  assert.equal(ui.retry.hidden, false);
  clearJoinFailure(ui.elements, "Ready.");
  assert.equal(ui.retry.hidden, true);
  assert.equal(ui.join.hidden, false);
});

test("an interrupted listener can retry without microphone or headphone setup", () => {
  const view = mapJoinError(new Error(JOIN_SIGNAL.JOIN_INTERRUPTED), { listenOnly: true });
  const ui = controls();
  applyJoinFailure(view, ui.elements);
  assert.equal(view.kind, "interrupted");
  assert.equal(ui.status.textContent, "Web Join was interrupted.");
  assert.equal(ui.hint.textContent, "Check your connection, then select Retry to listen again.");
  assert.equal(ui.retry.hidden, false);
  assert.equal(ui.retry.disabled, false);
  assert.equal(ui.join.hidden, true);
  assert.match(mapJoinError(new Error(JOIN_SIGNAL.JOIN_INTERRUPTED)).headline, /microphone/);
});

test("Retry wiring hides Join while failure chrome is active", () => {
  const ui = controls();
  applyJoinFailure(mapJoinError(new Error(JOIN_SIGNAL.HOST_NO_ANSWER)), ui.elements);
  assert.equal(ui.leave.hidden, true);
  assert.equal(ui.join.hidden, true);
  clearJoinFailure(ui.elements, "Your host invited you.");
  assert.equal(ui.join.hidden, false);
});
