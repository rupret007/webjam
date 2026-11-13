/** Room titles. A name is appended only when it is a short plain label. */

export const HEADPHONES_NOTE = "Headphones keep echo down if you talk.";

export function listeningLine(name) {
  const cleaned = cleanSessionName(name);
  return cleaned ? `You're listening. ${cleaned}` : "You're listening.";
}

export function joiningLine(name) {
  const cleaned = cleanSessionName(name);
  return cleaned ? `Joining ${cleaned}…` : "Joining the room…";
}

export function micOnLine(name) {
  const cleaned = cleanSessionName(name);
  return cleaned ? `You're in, ${cleaned}. Mic on` : "You're in, mic on";
}

export function isRoomTitle(message) {
  return typeof message === "string" && (/^You're listening\b/.test(message) || /^You're in\b/.test(message));
}

function cleanSessionName(name) {
  if (typeof name !== "string") return "";
  const cleaned = name.replace(/[\s\u00a0]+/g, " ").trim();
  if (!cleaned || cleaned.length > 40) return "";
  if (/[\u0000-\u001f\u007f]/.test(cleaned)) return "";
  if (/[\\/]|https?:|@/i.test(cleaned)) return "";
  if (/^[0-9a-f]{16,}$/i.test(cleaned)) return "";
  return cleaned;
}
