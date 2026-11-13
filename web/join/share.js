/** Invite delivery helpers. The share call itself stays in the click listener. */

export const INVITE_SHARE_TITLE = "Join me on WebJam";
export const INVITE_SHARE_TEXT = "Tap to listen, no app, no microphone.";
export const FIXTURE_INVITE_URL = "https://example.com/join/#invite";

/** One sentence plus the link. The sentence is what a stranger reads before they tap. */
export function inviteBody(url, text = INVITE_SHARE_TEXT) {
  return `${text}\n${url}`;
}

export function inviteMailto(url, title = INVITE_SHARE_TITLE, text = INVITE_SHARE_TEXT) {
  return `mailto:?subject=${encodeURIComponent(title)}&body=${encodeURIComponent(inviteBody(url, text))}`;
}

/** Reject a share outside the click that prepared it. A click listener must not use this. */
export function shareWithoutActivation(userActivation, currentEvent = null) {
  if (userActivation?.isActive !== true || !currentEvent) {
    throw new DOMException("A click is required to share.", "NotAllowedError");
  }
}
