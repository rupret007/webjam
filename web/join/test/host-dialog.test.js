import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  INVITE_SHARED_STATUS,
  markInviteShared,
  nextDialogTabTarget,
  tabbableDialogItems,
} from "../host.js";

const hostHtml = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "../host.html"),
  "utf8",
);

test("the invite dialog is marked modal", () => {
  assert.match(
    hostHtml,
    /id="invite-dialog"[^>]*role="dialog"[^>]*aria-modal="true"/,
  );
  assert.match(hostHtml, /id="invite-status"[^>]*tabindex="-1"/);
});

test("a successful share moves focus to the updated status", () => {
  const dialog = { hidden: true };
  const status = {
    textContent: "Invite ready. Your friend opens it and taps Join.",
    tabIndex: 0,
    focused: false,
    focus() {
      this.focused = true;
    },
  };
  markInviteShared(status, dialog);
  assert.equal(status.textContent, INVITE_SHARED_STATUS);
  assert.equal(dialog.hidden, false);
  assert.equal(status.tabIndex, -1);
  assert.equal(status.focused, true);
});

test("tab stays inside the invite dialog", () => {
  const share = { hidden: false, disabled: false, closest: () => null };
  const email = { hidden: true, disabled: false, closest: () => email };
  const copy = { hidden: false, disabled: false, closest: () => null };
  const dialog = {
    querySelectorAll: () => [share, email, copy],
  };
  const items = tabbableDialogItems(dialog);
  assert.deepEqual(items, [share, copy]);
  assert.equal(nextDialogTabTarget(items, copy, false), share);
  assert.equal(nextDialogTabTarget(items, share, true), copy);
  assert.equal(nextDialogTabTarget(items, share, false), null);
  const status = { hidden: false, disabled: false, closest: () => null };
  assert.equal(nextDialogTabTarget(items, status, false), share);
  assert.equal(nextDialogTabTarget(items, status, true), copy);
});
