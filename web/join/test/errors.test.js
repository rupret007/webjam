import test from "node:test";
import assert from "node:assert/strict";
import { JOIN_SIGNAL, mapJoinError, applyJoinFailure, clearJoinFailure } from "../errors.js";

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
  assert.match(ui.hint.textContent, /new browser invite/i);
  assert.equal(ui.retry.hidden, true);
  assert.equal(ui.join.hidden, true);
  assert.equal(ui.leave.hidden, true);
});

test("room full maps from the used-invite mailbox signal with Retry", () => {
  const view = mapJoinError(new Error(JOIN_SIGNAL.ROOM_FULL));
  assert.equal(view.kind, "room_full");
  assert.equal(view.showRetry, true);
  assert.match(view.headline, /already has a web guest/i);
  const ui = controls();
  applyJoinFailure(view, ui.elements);
  assert.equal(ui.retry.hidden, false);
  assert.equal(ui.join.hidden, true);
  assert.equal(ui.retry.disabled, false);
});

test("host offline maps from no-answer and authentication timeout signals with Retry", () => {
  for (const message of [JOIN_SIGNAL.HOST_NO_ANSWER, JOIN_SIGNAL.HOST_AUTH_TIMEOUT]) {
    const view = mapJoinError(new Error(message));
    assert.equal(view.kind, "host_offline");
    assert.equal(view.showRetry, true);
    assert.match(view.hint, /host/i);
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

test("expired invite and relay session map with Retry and fresh-invite hints", () => {
  const invite = mapJoinError(new Error(JOIN_SIGNAL.INVITE_EXPIRED));
  assert.equal(invite.kind, "invite_expired");
  assert.equal(invite.needsFreshInvite, true);
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

test("Retry wiring hides Join while failure chrome is active", () => {
  const ui = controls();
  applyJoinFailure(mapJoinError(new Error(JOIN_SIGNAL.HOST_NO_ANSWER)), ui.elements);
  assert.equal(ui.leave.hidden, true);
  assert.equal(ui.join.hidden, true);
  clearJoinFailure(ui.elements, "Your host invited you.");
  assert.equal(ui.join.hidden, false);
});
