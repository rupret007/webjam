/** Maps existing join failure messages to first-time-friendly UI copy. */

export const JOIN_SIGNAL = Object.freeze({
  ROOM_FULL: "This invitation was already used or expired. Ask the host for a new link.",
  SESSION_ENDED: "The Web Join connection ended.",
  MICROPHONE_ENDED: "Microphone disconnected.",
  HOST_NO_ANSWER: "The host did not answer in time. Ask for a new invite.",
  HOST_AUTH_TIMEOUT: "Host authentication timed out. Ask for a new invite.",
  INVITE_EXPIRED: "The invite expired. Ask the host for a new link.",
  INVITE_MAILBOX_EXPIRED: "This invitation expired. Ask the host for a new link.",
  INVITE_INVALID: "This Web Join invite is invalid or expired. Ask the host for a new link.",
  HOST_BINDING: "Host identity or invite binding did not match",
  RELAY_SESSION_EXPIRED: "The free relay session expired. Ask the host for a new invite.",
  RELAY_CAP_REACHED: "Web Join reached a usage limit.",
  RELAY_UNAVAILABLE: "Web Join is unavailable. Try again later or ask your host for help.",
  ICE_GATHER_TIMEOUT: "Network discovery timed out. Try another network or the app.",
  ICE_CONNECTION_FAILED: "Connection ended. Ask the host for a new invite.",
  ICE_DISCONNECTED: "The network connection timed out.",
  NO_NETWORK_PATH: "No permitted network path. Try the app or another network.",
  JOIN_INTERRUPTED: "Joining timed out or was interrupted. Check your connection and microphone, then try Join again.",
});

const NETWORK_SIGNALS = new Set([
  JOIN_SIGNAL.ICE_GATHER_TIMEOUT,
  JOIN_SIGNAL.ICE_CONNECTION_FAILED,
  JOIN_SIGNAL.ICE_DISCONNECTED,
  JOIN_SIGNAL.NO_NETWORK_PATH,
]);

const EXPIRED_SIGNALS = new Set([
  JOIN_SIGNAL.INVITE_EXPIRED,
  JOIN_SIGNAL.INVITE_MAILBOX_EXPIRED,
  JOIN_SIGNAL.INVITE_INVALID,
  JOIN_SIGNAL.RELAY_SESSION_EXPIRED,
]);

const HOST_OFFLINE_SIGNALS = new Set([
  JOIN_SIGNAL.HOST_NO_ANSWER,
  JOIN_SIGNAL.HOST_AUTH_TIMEOUT,
]);

const RELAY_CONFIGURATION_SIGNALS = new Set([
  "Untrusted relay configuration",
  "Untrusted relay address",
  "Invalid relay configuration",
  "Invalid relay credential",
]);

const FRESH_INVITE_HINT = "Ask your host to send a new browser invite. If you joined from Hang Out, return to your Jam room to accept it.";

/** @typedef {{ kind: string, headline: string, hint: string, showRetry: boolean, needsFreshInvite: boolean }} JoinFailureView */

/** @param {unknown} error @param {{ listenOnly?: boolean }} [options] @returns {JoinFailureView} */
export function mapJoinError(error, { listenOnly = false } = {}) {
  const message = error instanceof Error ? error.message : String(error ?? "");
  if (message === JOIN_SIGNAL.MICROPHONE_ENDED) {
    return {
      kind: "microphone_ended",
      headline: message,
      hint: "Reconnect your microphone or choose another input. Ask your host for a new browser invite.",
      showRetry: false,
      needsFreshInvite: true,
    };
  }
  if (message === JOIN_SIGNAL.SESSION_ENDED) {
    return {
      kind: "session_ended",
      headline: message,
      hint: FRESH_INVITE_HINT,
      showRetry: false,
      needsFreshInvite: true,
    };
  }
  if (message === JOIN_SIGNAL.ROOM_FULL) {
    return {
      kind: "room_full",
      headline: "This browser invite could not be used.",
      hint: "Ask your host for a fresh Web Join link. If another browser guest is connected, they’ll need to leave first.",
      showRetry: false,
      needsFreshInvite: true,
    };
  }
  if (HOST_OFFLINE_SIGNALS.has(message)) {
    return {
      kind: "host_offline",
      headline: "Your host is not ready for Web Join yet.",
      hint: "Make sure the WebJam app is hosting, Web Join is allowed, and your host copied a fresh invite. Then tap Retry.",
      showRetry: true,
      needsFreshInvite: false,
    };
  }
  if (message === JOIN_SIGNAL.HOST_BINDING) {
    return {
      kind: "host_binding",
      headline: "WebJam couldn't verify the host for this invite.",
      hint: FRESH_INVITE_HINT,
      showRetry: false,
      needsFreshInvite: true,
    };
  }
  if (RELAY_CONFIGURATION_SIGNALS.has(message)) {
    return {
      kind: "relay_configuration",
      headline: "WebJam couldn't verify its browser audio relay.",
      hint: "Tell your host about this error. Ask for a new browser invite after the relay is working.",
      showRetry: false,
      needsFreshInvite: true,
    };
  }
  if (NETWORK_SIGNALS.has(message)) {
    const relayFailed = message === JOIN_SIGNAL.ICE_CONNECTION_FAILED || message === JOIN_SIGNAL.ICE_DISCONNECTED;
    return {
      kind: "network_blocked",
      headline: relayFailed
        ? "WebJam could not reach your host, even through the free relay."
        : "This network blocked the direct connection to your host.",
      hint: relayFailed
        ? "Try another network or a phone hotspot, then tap Retry. Web Join will request fresh relay credentials."
        : "WebJam is trying the free relay next. If joining still fails, try another network or a phone hotspot, then tap Retry.",
      showRetry: true,
      needsFreshInvite: false,
    };
  }
  if (EXPIRED_SIGNALS.has(message)) {
    return mapExpired(message);
  }
  if (message === JOIN_SIGNAL.RELAY_CAP_REACHED) {
    const seconds = error instanceof Error ? error.retryAfterSeconds : null;
    const dailyReset = Number.isInteger(seconds) && seconds >= 1 && seconds <= 86400
      ? new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }).format(Date.now() + seconds * 1000)
      : null;
    return {
      kind: "relay_cap",
      headline: message,
      hint: dailyReset
        ? `This daily request allowance resets around ${dailyReset}. Your host can keep the Music room open and share a fresh browser invite afterward.`
        : "Your host can keep the Music room open. Ask for a new browser invite when Web Join is available again.",
      showRetry: false,
      needsFreshInvite: true,
    };
  }
  if (message === JOIN_SIGNAL.RELAY_UNAVAILABLE) {
    return {
      kind: "relay_unavailable",
      headline: JOIN_SIGNAL.RELAY_UNAVAILABLE,
      hint: "The shared relay is busy or offline. Wait a minute, then tap Retry to fetch new credentials.",
      showRetry: true,
      needsFreshInvite: false,
    };
  }
  if (message === JOIN_SIGNAL.JOIN_INTERRUPTED || error instanceof Error && error.name === "AbortError") {
    return {
      kind: "interrupted",
      headline: listenOnly ? "Web Join was interrupted." : JOIN_SIGNAL.JOIN_INTERRUPTED,
      hint: listenOnly
        ? "Check your connection, then select Retry to listen again."
        : "Stay on this page, keep headphones on, and tap Retry when you are ready.",
      showRetry: true,
      needsFreshInvite: false,
    };
  }
  return {
    kind: "unknown",
    headline: message || "Web Join could not connect.",
    hint: "Check your connection and invite link, then tap Retry or ask your host for help.",
    showRetry: true,
    needsFreshInvite: false,
  };
}

/** @param {string} message @returns {JoinFailureView} */
function mapExpired(message) {
  const turnOnly = message === JOIN_SIGNAL.RELAY_SESSION_EXPIRED;
  const inviteHeadline = message === JOIN_SIGNAL.INVITE_EXPIRED
    ? "This browser invite has expired."
    : "This invite link is no longer valid.";
  return {
    kind: turnOnly ? "turn_expired" : "invite_expired",
    headline: turnOnly ? "The free relay session timed out." : inviteHeadline,
    hint: turnOnly
      ? "Ask your host for a new invite if Retry does not connect. Retry fetches fresh relay credentials when the link is still good."
      : "Ask your host for a new Web Join link and open it to join.",
    showRetry: turnOnly,
    needsFreshInvite: !turnOnly,
  };
}

/** @param {JoinFailureView} view @param {{ status: HTMLElement, hint: HTMLElement, retry: HTMLButtonElement, join: HTMLButtonElement, leave: HTMLButtonElement }} elements */
export function applyJoinFailure(view, elements) {
  elements.status.textContent = view.headline;
  elements.hint.textContent = view.hint;
  elements.hint.hidden = !view.hint;
  elements.retry.hidden = !view.showRetry;
  elements.join.hidden = view.showRetry || view.needsFreshInvite;
  elements.leave.hidden = true;
  elements.retry.disabled = !view.showRetry;
  if (view.showRetry) elements.join.disabled = false;
}

/** @param {{ status: HTMLElement, hint: HTMLElement, retry: HTMLButtonElement, join: HTMLButtonElement }} elements @param {string} message */
export function clearJoinFailure(elements, message) {
  elements.status.textContent = message;
  elements.hint.textContent = "";
  elements.hint.hidden = true;
  elements.retry.hidden = true;
  elements.join.hidden = false;
}
