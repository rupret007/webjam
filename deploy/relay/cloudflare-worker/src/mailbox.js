/**
 * One Durable Object per session mailbox. Slots are write-once opaque
 * ciphertext. The Worker never sees the capability, AEAD key, or plaintext.
 */

export const DEFAULT_TTL_SECONDS = 600;
export const MAX_PAYLOAD_BYTES = 4096;
export const MAX_BODY_BYTES = MAX_PAYLOAD_BYTES + 64;
export const MAX_WAITERS = 2;
export const MAILBOX_ID_RE = /^[0-9a-f]{64}$/;

export function validMailboxId(value) {
  return MAILBOX_ID_RE.test(String(value ?? ""));
}

export function validPayload(value) {
  const text = String(value ?? "");
  if (!text || text.length > MAX_PAYLOAD_BYTES) {
    return false;
  }
  return /^[A-Za-z0-9_-]+$/.test(text);
}

export function emptyMailbox() {
  return { createdAt: 0, offer: "", answer: "", consumed: false };
}

export function applyMailbox(state, action, nowMs, ttlMs = DEFAULT_TTL_SECONDS * 1000) {
  if (state == null || typeof state !== "object") {
    return { status: 503, code: "unreadable" };
  }
  if (!Number.isFinite(nowMs) || nowMs < 0) {
    return { status: 503, code: "unreadable" };
  }
  if (state.createdAt && nowMs - state.createdAt > ttlMs) {
    return { status: 410, code: "expired", clear: true };
  }
  if (state.consumed && action.kind !== "delete") {
    return { status: 410, code: "gone" };
  }
  if (action.kind === "put") {
    if (action.slot !== "offer" && action.slot !== "answer") {
      return { status: 404, code: "not_found" };
    }
    if (!validPayload(action.payload)) {
      return { status: 400, code: "payload" };
    }
    if (state[action.slot]) {
      return { status: 409, code: "duplicate" };
    }
    if (!state.createdAt) {
      state.createdAt = nowMs;
    }
    state[action.slot] = action.payload;
    return { status: 201, code: "ok", notify: action.slot === "answer" };
  }
  if (action.kind === "get") {
    if (action.slot !== "offer" && action.slot !== "answer") {
      return { status: 404, code: "not_found" };
    }
    const payload = state[action.slot];
    if (!payload) {
      return { status: 204, code: "empty" };
    }
    return { status: 200, code: "ok", payload };
  }
  if (action.kind === "delete") {
    if (!state.answer) {
      return { status: 404, code: "not_found" };
    }
    return { status: 204, code: "gone", clear: true };
  }
  return { status: 404, code: "not_found" };
}

function jsonResponse(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json",
      "cache-control": "no-store",
    },
  });
}

export function validContentLength(value) {
  const length = Number(value);
  return Number.isFinite(length) && length >= 1 && length <= MAX_BODY_BYTES;
}

export function waiterAccepted(openCount) {
  return Number(openCount) < MAX_WAITERS;
}

export class Mailbox {
  constructor(ctx) {
    this.ctx = ctx;
  }

  async fetch(request) {
    try {
      return await this._handle(request);
    } catch {
      return jsonResponse(503, { error: "unavailable" });
    }
  }

  async _handle(request) {
    const url = new URL(request.url);
    const nowMs = Number(request.headers.get("X-WebJam-Now") ?? Date.now());
    const parts = url.pathname.split("/").filter(Boolean);
    const slot = parts[parts.length - 1] || "";
    let action;
    if (request.method === "DELETE") {
      action = { kind: "delete" };
    } else if (request.headers.get("Upgrade") === "websocket") {
      return this._acceptWait();
    } else if (request.method === "POST") {
      if (!validContentLength(request.headers.get("content-length"))) {
        return jsonResponse(400, { error: "payload" });
      }
      let payload = "";
      try {
        const body = await request.json();
        payload = String(body?.payload ?? "");
      } catch {
        return jsonResponse(400, { error: "payload" });
      }
      action = { kind: "put", slot, payload };
    } else if (request.method === "GET") {
      action = { kind: "get", slot };
    } else {
      return jsonResponse(405, { error: "method" });
    }
    const stored = await this.ctx.storage.get("mailbox");
    const state = stored && typeof stored === "object" ? stored : emptyMailbox();
    const result = applyMailbox(state, action, nowMs);
    if (result.clear) {
      await this.ctx.storage.deleteAll();
    } else if (result.status === 201) {
      await this.ctx.storage.put("mailbox", state);
      await this.ctx.storage.setAlarm(state.createdAt + DEFAULT_TTL_SECONDS * 1000);
    }
    if (result.notify) {
      this._notifyWaiters();
    }
    if (result.status === 204) {
      return new Response(null, { status: 204, headers: { "cache-control": "no-store" } });
    }
    const body = result.payload
      ? { payload: result.payload }
      : { error: result.code };
    return jsonResponse(result.status, body);
  }

  async alarm() {
    await this.ctx.storage.deleteAll();
  }

  async webSocketMessage() {
    return undefined;
  }

  async webSocketClose() {
    return undefined;
  }

  _acceptWait() {
    const open = this.ctx.getWebSockets ? this.ctx.getWebSockets() : [];
    if (!waiterAccepted(open.length)) {
      return jsonResponse(429, { error: "rate" });
    }
    const pair = new WebSocketPair();
    this.ctx.acceptWebSocket(pair[1]);
    return new Response(null, { status: 101, webSocket: pair[0] });
  }

  _notifyWaiters() {
    for (const socket of this.ctx.getWebSockets()) {
      try {
        socket.send("ready");
        socket.close(1000, "ready");
      } catch {
        // Hibernated sockets can already be gone.
      }
    }
  }
}
