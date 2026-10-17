const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });
export const DOMAIN = "webjam/web-join/1";
export const MAX_ENVELOPE = 8192;
export const toHex = (bytes) => Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
export function fromHex(value) {
  if (typeof value !== "string" || !/^(?:[0-9a-f]{2})+$/.test(value)) throw new Error("Invalid hexadecimal value");
  return Uint8Array.from(value.match(/../g), (pair) => parseInt(pair, 16));
}
export const toBase64Url = (bytes) => btoa(String.fromCharCode(...bytes)).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
export function fromBase64Url(value) {
  if (typeof value !== "string" || !/^[A-Za-z0-9_-]+$/.test(value)) throw new Error("Invalid encoded value");
  const bytes = Uint8Array.from(atob(value.replaceAll("-", "+").replaceAll("_", "/")), (character) => character.charCodeAt(0));
  if (toBase64Url(bytes) !== value) throw new Error("Noncanonical encoded value");
  return bytes;
}
export async function hkdf(capability, salt, info) {
  if (capability.length !== 32) throw new Error("Invalid capability");
  const key = await crypto.subtle.importKey("raw", capability, "HKDF", false, ["deriveBits"]);
  return new Uint8Array(await crypto.subtle.deriveBits({ name: "HKDF", hash: "SHA-256", salt, info: encoder.encode(info) }, key, 256));
}
export function readInvite(hash, now = Date.now()) {
  const values = new URLSearchParams(hash.replace(/^#/, ""));
  if ([...values.keys()].length !== 4 || ["k", "s", "g", "e"].some((key) => values.getAll(key).length !== 1)) throw new Error("Invalid Web Join invite");
  const capability = fromBase64Url(values.get("k"));
  const session = values.get("s");
  const generation = Number(values.get("g"));
  const expiresAt = Number(values.get("e"));
  if (capability.length !== 32 || capability.every((byte) => byte === 0) || !/^[0-9a-f]{64}$/.test(session) || /^0+$/.test(session) || !/^[1-9][0-9]*$/.test(values.get("g")) || !Number.isSafeInteger(generation) || generation > 4294967295 || !/^[1-9][0-9]*$/.test(values.get("e")) || !Number.isSafeInteger(expiresAt) || expiresAt * 1000 <= now || expiresAt * 1000 > now + 630000) throw new Error("This Web Join invite is invalid or expired. Ask the host for a new link.");
  return { capability, session, generation, expiresAt };
}
export async function mailboxId(invite, kind, index, round = "direct") {
  if (!["offer", "answer"].includes(kind) || round !== "direct" || !Number.isInteger(index) || index < 0 || index > 2) throw new Error("Invalid mailbox slot");
  const root = await hkdf(invite.capability, fromHex(invite.session), DOMAIN);
  return toHex(await hkdf(root, new Uint8Array(), `webjam/v3/mailbox/id/${round}/${kind}/${index}`));
}
export async function turnToken(invite) {
  return toBase64Url(await hkdf(invite.capability, fromHex(invite.session), `${DOMAIN}/turn-token`));
}
async function aesKey(invite) {
  const bytes = await hkdf(invite.capability, fromHex(invite.session), `${DOMAIN}/aead`);
  return crypto.subtle.importKey("raw", bytes, "AES-GCM", false, ["encrypt", "decrypt"]);
}
const aad = (invite) => new Uint8Array([...encoder.encode(`${DOMAIN}/aad`), ...fromHex(invite.session)]);
export async function seal(invite, value, nonce = crypto.getRandomValues(new Uint8Array(12))) {
  if (nonce.length !== 12) throw new Error("Invalid encryption nonce");
  const ciphertext = await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce, additionalData: aad(invite) }, await aesKey(invite), encoder.encode(JSON.stringify(value)));
  const bytes = new Uint8Array([...encoder.encode("WJWS1"), ...nonce, ...new Uint8Array(ciphertext)]);
  if (bytes.length > MAX_ENVELOPE) throw new Error("Signaling exceeds the session limit");
  return bytes;
}
export async function open(invite, bytes) {
  if (!(bytes instanceof Uint8Array) || bytes.length < 34 || bytes.length > MAX_ENVELOPE || decoder.decode(bytes.subarray(0, 5)) !== "WJWS1") throw new Error("Invalid encrypted signaling");
  return JSON.parse(decoder.decode(await crypto.subtle.decrypt({ name: "AES-GCM", iv: bytes.subarray(5, 17), additionalData: aad(invite) }, await aesKey(invite), bytes.subarray(17))));
}
export function sdpFingerprint(sdp) {
  if (typeof sdp !== "string" || sdp.length > MAX_ENVELOPE) throw new Error("Invalid audio description");
  const lines = sdp.split(/\r?\n/).filter((line) => line.startsWith("a=fingerprint:"));
  const pins = lines.map((line) => {
    if (!/^a=fingerprint:sha-256 (?:[A-Fa-f0-9]{2}:){31}[A-Fa-f0-9]{2}$/.test(line)) throw new Error("Unsupported DTLS fingerprint");
    return line.slice("a=fingerprint:sha-256 ".length).replaceAll(":", "").toLowerCase();
  });
  if (!pins.length || pins.some((pin) => pin !== pins[0])) throw new Error("Conflicting DTLS fingerprints");
  return pins[0];
}
export function validateOffer(invite, offer, now = Date.now()) {
  const fields = ["v", "kind", "session", "generation", "expires_at", "nonce", "host_pin", "guest_pin", "sdp"];
  if (!offer || Object.keys(offer).length !== fields.length || fields.some((key) => !Object.hasOwn(offer, key)) || offer.v !== 1 || offer.kind !== "offer" || offer.session !== invite.session || offer.generation !== invite.generation || offer.expires_at !== invite.expiresAt || offer.expires_at * 1000 <= now || !/^[0-9a-f]{32}$/.test(offer.nonce) || !/^[0-9a-f]{64}$/.test(offer.host_pin) || offer.guest_pin !== "" || sdpFingerprint(offer.sdp) !== offer.host_pin) throw new Error("Host identity or invite binding did not match");
  return offer;
}
export async function enrollmentProof(invite, answer) {
  const key = await crypto.subtle.importKey("raw", await hkdf(invite.capability, fromHex(invite.session), `${DOMAIN}/enrollment`), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const body = `${DOMAIN}/enrollment\n${answer.session}\nguest\n${answer.generation}\n${answer.expires_at}\n${answer.nonce}\n${answer.host_pin}\n${answer.guest_pin}`;
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, encoder.encode(body)));
}
