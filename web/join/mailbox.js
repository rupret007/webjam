import { fromBase64Url, toBase64Url, mailboxId, turnToken, MAX_ENVELOPE } from "./crypto.js";
import { validateCredentials, RELAY_UNAVAILABLE_MESSAGE } from "./policy.js";
export const BUSY_MESSAGE = RELAY_UNAVAILABLE_MESSAGE;
async function boundedText(response, limit = 16384) {
  const length = response.headers.get("content-length");
  if (length !== null && (!/^\d+$/.test(length) || Number(length) > limit)) throw new Error("Relay response exceeds the limit");
  if (!response.body) return "";
  const reader = response.body.getReader(), chunks = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) { await reader.cancel(); throw new Error("Relay response exceeds the limit"); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
}
export function delay(ms, signal) {
  return new Promise((resolve, reject) => {
    const cleanup = () => { clearTimeout(timer); signal?.removeEventListener("abort", abort); };
    const abort = () => { cleanup(); reject(new DOMException("Stopped", "AbortError")); };
    const timer = setTimeout(() => { cleanup(); resolve(); }, ms);
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) abort();
  });
}
export class Mailbox {
  constructor(origin, invite, { signal, fetchImpl = globalThis.fetch, interval = 2500, timeout = 12000 } = {}) {
    const url = new URL(origin);
    if (url.origin !== origin || (url.protocol !== "https:" && !(url.protocol === "http:" && ["localhost", "127.0.0.1"].includes(url.hostname)))) throw new Error("Invalid relay origin");
    Object.assign(this, { origin, invite, signal, fetchImpl, interval, timeout });
    // Safari requires the Window receiver even when fetch is stored on a client.
    this.fetchImpl = fetchImpl.bind(globalThis);
    this.tail = Promise.resolve(); this.nextAt = 0;
  }
  request(path, init = {}) {
    const operation = this.tail.then(async () => {
      await delay(Math.max(0, this.nextAt - Date.now()), this.signal);
      if (Date.now() >= this.invite.expiresAt * 1000) throw new Error("The invite expired. Ask the host for a new link.");
      this.nextAt = Date.now() + this.interval;
      const controller = new AbortController();
      const abort = () => controller.abort();
      this.signal?.addEventListener("abort", abort, { once: true });
      if (this.signal?.aborted) abort();
      const timer = setTimeout(abort, this.timeout);
      try {
        const response = await this.fetchImpl(`${this.origin}${path}`, { ...init, credentials: "omit", referrerPolicy: "no-referrer", cache: "no-store", redirect: "error", signal: controller.signal });
        if ([429, 503].includes(response.status)) throw new Error(BUSY_MESSAGE);
        if ([204, 410].includes(response.status)) return { status: response.status, body: null };
        const text = await boundedText(response);
        return { status: response.status, body: JSON.parse(text) };
      } finally {
        clearTimeout(timer); this.signal?.removeEventListener("abort", abort);
      }
    });
    this.tail = operation.catch(() => {});
    return operation;
  }
  async path(kind, index) { return `/mailbox/${await mailboxId(this.invite, kind, index)}/${kind}?client=web`; }
  async send(kind, bytes) {
    if (!(bytes instanceof Uint8Array) || bytes.length < 34 || bytes.length > MAX_ENVELOPE) throw new Error("Invalid encrypted signaling");
    const count = Math.ceil(bytes.length / 2998);
    for (const index of [...Array.from({ length: count - 1 }, (_, i) => i + 1), 0]) {
      const payload = toBase64Url(new Uint8Array([index, count, ...bytes.subarray(index * 2998, (index + 1) * 2998)]));
      const result = await this.request(await this.path(kind, index), { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ payload }) });
      if (result.status !== 201) throw new Error("This invitation was already used or expired. Ask the host for a new link.");
    }
  }
  async receive(kind, timeout = 90000) {
    const until = Math.min(Date.now() + timeout, this.invite.expiresAt * 1000);
    let first;
    while (Date.now() < until) {
      const result = await this.request(await this.path(kind, 0));
      if (result.status === 200) { first = this.chunk(result, 0); break; }
      if (result.status !== 204) throw new Error("This invitation expired. Ask the host for a new link.");
      await delay(Math.min(5000, Math.max(0, until - Date.now())), this.signal);
    }
    if (!first) throw new Error("The host did not answer in time. Ask for a new invite.");
    const parts = [first.subarray(2)];
    for (let index = 1; index < first[1]; index++) {
      const part = this.chunk(await this.request(await this.path(kind, index)), index, first[1]);
      parts.push(part.subarray(2));
    }
    const bytes = new Uint8Array(parts.flatMap((part) => Array.from(part)));
    if (bytes.length > MAX_ENVELOPE) throw new Error("Signaling exceeds the limit");
    return bytes;
  }
  chunk(result, index, count = null) {
    if (result.status !== 200 || typeof result.body?.payload !== "string" || result.body.payload.length > 4000) throw new Error("Invalid signaling chunk");
    const chunk = fromBase64Url(result.body.payload);
    if (chunk.length < 3 || chunk.length > 3000 || chunk[0] !== index || chunk[1] < 1 || chunk[1] > 3 || index >= chunk[1] || (count !== null && chunk[1] !== count) || (index < chunk[1] - 1 && chunk.length !== 3000)) throw new Error("Invalid signaling chunk");
    return chunk;
  }
  async credentials() {
    const result = await this.request("/turn-credentials", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ session_id: this.invite.session, invite_token: await turnToken(this.invite), client: "web" }) });
    if (result.status !== 200) throw new Error(BUSY_MESSAGE);
    return validateCredentials(result.body);
  }
}
