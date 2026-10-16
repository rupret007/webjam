const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });
export const MAX_PAYLOAD = 4096;

export function toBase64Url(bytes) {
  return btoa(String.fromCharCode(...bytes)).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

export function fromBase64Url(text) {
  if (typeof text !== "string" || !/^[A-Za-z0-9_-]+$/.test(text)) throw new Error("Invalid encoded data");
  const result = Uint8Array.from(atob(text.replaceAll("-", "+").replaceAll("_", "/")), (ch) => ch.charCodeAt(0));
  if (toBase64Url(result) !== text) throw new Error("Noncanonical encoded data");
  return result;
}

export function fromHex(text) {
  if (!/^(?:[0-9a-f]{2})+$/.test(text)) throw new Error("Invalid hex data");
  return Uint8Array.from(text.match(/../g), (byte) => parseInt(byte, 16));
}

export function toHex(bytes) {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function fixed(bytes, length) {
  if (!(bytes instanceof Uint8Array) || bytes.length !== length) throw new Error("Invalid key length");
  return bytes;
}

export async function hkdf(capability, salt, info, length = 32) {
  const material = await crypto.subtle.importKey("raw", fixed(capability, 32), "HKDF", false, ["deriveBits"]);
  return new Uint8Array(await crypto.subtle.deriveBits({ name: "HKDF", hash: "SHA-256", salt, info: encoder.encode(info) }, material, length * 8));
}

export async function mailboxId(capability) {
  return toHex(await hkdf(capability, new Uint8Array(), "webjam/v3/mailbox/id"));
}

export function mailboxKey(capability, sessionReference) {
  return hkdf(capability, fixed(sessionReference, 16), "webjam/v3/mailbox/aead");
}

export function desktopSlotAad(kind, sessionReference, hostFingerprint) {
  if (!["offer", "answer"].includes(kind)) throw new Error("Invalid mailbox slot");
  const prefix = encoder.encode(`webjam/v3/mailbox/${kind}\0`);
  return new Uint8Array([...prefix, ...fixed(sessionReference, 16), ...fixed(hostFingerprint, 32)]);
}

export async function seal(key, plaintext, aad) {
  const nonce = crypto.getRandomValues(new Uint8Array(12));
  const imported = await crypto.subtle.importKey("raw", fixed(key, 32), "AES-GCM", false, ["encrypt"]);
  const encrypted = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce, additionalData: aad }, imported, encoder.encode(plaintext)));
  const payload = toBase64Url(new Uint8Array([...nonce, ...encrypted]));
  if (payload.length > MAX_PAYLOAD) throw new Error("Signaling message exceeds mailbox limit");
  return payload;
}

export async function open(key, payload, aad) {
  if (typeof payload !== "string" || payload.length > MAX_PAYLOAD) throw new Error("Invalid mailbox payload");
  const bytes = fromBase64Url(payload);
  if (bytes.length < 29) throw new Error("Invalid mailbox payload");
  const imported = await crypto.subtle.importKey("raw", fixed(key, 32), "AES-GCM", false, ["decrypt"]);
  return decoder.decode(await crypto.subtle.decrypt({ name: "AES-GCM", iv: bytes.subarray(0, 12), additionalData: aad }, imported, bytes.subarray(12)));
}

export function sessionBytes(sessionId) {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(sessionId)) throw new Error("Invalid session");
  return fromHex(sessionId.replaceAll("-", ""));
}

export async function roomAddress(capability, sessionId, address, slot) {
  if (!["offer", "answer"].includes(slot)) throw new Error("Invalid mailbox slot");
  const reference = sessionBytes(sessionId);
  const child = await hkdf(capability, reference, `webjam/sketch/1/${address}`);
  return {
    id: await mailboxId(child),
    key: await mailboxKey(child, reference),
    aad: encoder.encode(`webjam/sketch/1/${sessionId}/${address}/${slot}`),
  };
}

export async function turnToken(capability, sessionId) {
  return toBase64Url(await hkdf(capability, sessionBytes(sessionId), "webjam/sketch/1/turn-token"));
}

export function createInvite(now = Date.now()) {
  return { capability: crypto.getRandomValues(new Uint8Array(32)), sessionId: crypto.randomUUID(), created: now };
}

export function inviteFragment(invite) {
  return new URLSearchParams({ k: toBase64Url(invite.capability), s: invite.sessionId, t: String(invite.created) }).toString();
}

export function readInvite(hash, now = Date.now()) {
  const values = new URLSearchParams(hash.replace(/^#/, ""));
  if ([...values.keys()].some((key) => !["k", "s", "t"].includes(key)) || [...values.keys()].length !== 3) throw new Error("Invalid invite link");
  const capability = fixed(fromBase64Url(values.get("k")), 32);
  const sessionId = values.get("s");
  sessionBytes(sessionId);
  const created = Number(values.get("t"));
  if (!Number.isSafeInteger(created) || created > now + 30_000 || now - created >= 600_000) throw new Error("This invite has expired. Create a new Sketch Jam link.");
  return { capability, sessionId, created };
}
