import { FIXTURE_INVITE_URL, INVITE_SHARE_TITLE, inviteMailto, shareWithoutActivation } from "./share.js";

export const INVITE_SHARED_STATUS = "Invite shared. Your friend opens it and taps Join.";

export function tabbableDialogItems(dialog) {
  return [...dialog.querySelectorAll("button, a[href], input, select, textarea")].filter((node) => {
    if (node.disabled || node.hidden) return false;
    return !node.closest("[hidden]");
  });
}

/** Return the control that should take focus, or null to leave the browser default. */
export function nextDialogTabTarget(items, active, shiftKey) {
  if (!items.length) return null;
  const first = items[0];
  const last = items[items.length - 1];
  const inside = items.includes(active);
  if (shiftKey) return !inside || active === first ? last : null;
  return !inside || active === last ? first : null;
}

export function markInviteShared(status, dialog) {
  status.textContent = INVITE_SHARED_STATUS;
  if (dialog) dialog.hidden = false;
  status.tabIndex = -1;
  status.focus();
}

function bootHostInvite() {
  if (typeof document === "undefined") return;
  const prepared = { title: INVITE_SHARE_TITLE, text: INVITE_SHARE_TITLE, url: FIXTURE_INVITE_URL };
  const shareButton = document.getElementById("share-invite");
  const emailLink = document.getElementById("email-invite");
  const copyButton = document.getElementById("copy-invite");
  const dialog = document.getElementById("invite-dialog");
  const status = document.getElementById("invite-status");

  emailLink.href = inviteMailto(prepared.url, prepared.title);

  const canShare = typeof navigator.share === "function";
  shareButton.hidden = !canShare;
  emailLink.hidden = canShare;
  emailLink.className = canShare ? "action secondary" : "action";
  status.tabIndex = -1;

  dialog.addEventListener("keydown", (event) => {
    if (event.key !== "Tab" || dialog.hidden) return;
    const target = nextDialogTabTarget(
      tabbableDialogItems(dialog),
      document.activeElement,
      event.shiftKey,
    );
    if (!target) return;
    event.preventDefault();
    target.focus();
  });

  // The invitation is already prepared. Loading this page must not call navigator.share.
  shareButton.addEventListener("click", () => {
    let pending;
    try {
      pending = navigator.share(prepared);
    } catch (error) {
      pending = Promise.reject(error);
    }
    Promise.resolve(pending).then(() => {
      markInviteShared(status, dialog);
    }, () => {
      copyButton.disabled = false;
    });
  });

  emailLink.addEventListener("click", (event) => {
    event.preventDefault();
    window.open(emailLink.href, "_self");
  });

  copyButton.addEventListener("click", () => {
    const write = navigator.clipboard?.writeText?.(prepared.url);
    if (write && typeof write.then === "function") {
      write.then(() => {
        copyButton.disabled = false;
      }, () => {
        copyButton.disabled = false;
      });
    }
  });

  window.webjamShareInvite = () => {
    shareWithoutActivation(navigator.userActivation, window.event);
    return navigator.share(prepared);
  };

  const first = tabbableDialogItems(dialog)[0];
  if (first) first.focus();
}

bootHostInvite();
