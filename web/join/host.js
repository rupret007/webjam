import { FIXTURE_INVITE_URL, INVITE_SHARE_TITLE, inviteMailto, shareWithoutActivation } from "./share.js";

const prepared = { title: INVITE_SHARE_TITLE, text: INVITE_SHARE_TITLE, url: FIXTURE_INVITE_URL };
const shareButton = document.getElementById("share-invite");
const emailLink = document.getElementById("email-invite");
const copyButton = document.getElementById("copy-invite");
const dialog = document.getElementById("invite-dialog");

emailLink.href = inviteMailto(prepared.url, prepared.title);

const canShare = typeof navigator.share === "function";
shareButton.hidden = !canShare;
emailLink.hidden = canShare;
emailLink.className = canShare ? "action secondary" : "action";

// The invitation is already prepared. Loading this page must not call navigator.share.
shareButton.addEventListener("click", () => {
  let pending;
  try {
    pending = navigator.share(prepared);
  } catch (error) {
    pending = Promise.reject(error);
  }
  Promise.resolve(pending).then(() => {
    dialog.hidden = true;
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
