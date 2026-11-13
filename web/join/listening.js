/** First in-room line. A name is appended only when it is a short plain label. */

export function listeningLine(name) {
  const cleaned = cleanSessionName(name);
  return cleaned ? `You're listening. ${cleaned}` : "You're listening.";
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
