/** Invite delivery helpers. The share call itself stays in the click listener. */

export const INVITE_SHARE_TITLE = "Join me on WebJam";
export const FIXTURE_INVITE_URL = "https://example.com/join/#invite";

export function inviteMailto(url, title = INVITE_SHARE_TITLE) {
  return `mailto:?subject=${encodeURIComponent(title)}&body=${encodeURIComponent(url)}`;
}

/** Reject a share outside the click that prepared it. A click listener must not use this. */
export function shareWithoutActivation(userActivation, currentEvent = null) {
  if (userActivation?.isActive !== true || !currentEvent) {
    throw new DOMException("A click is required to share.", "NotAllowedError");
  }
}
