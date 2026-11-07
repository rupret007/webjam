// Local test transport only. The browser uses the real Worker request handler
// and real Mailbox state machine; neither WebRTC nor signaling is fabricated.
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { extname, resolve, sep } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { Mailbox } from "../../../../deploy/relay/cloudflare-worker/src/mailbox.js";
import { MailboxQuota } from "../../../../deploy/relay/cloudflare-worker/src/quota.js";
import { handleRequest, resetWorkerState } from "../../../../deploy/relay/cloudflare-worker/src/worker.js";

const root = fileURLToPath(new URL("../../", import.meta.url));
const origin = "http://127.0.0.1:4173";
const rooms = new Map();
const quotas = new Map();
let audit = [];
let fault = null;

function memoryObject(ObjectType, withSql = false) {
  const data = new Map();
  const database = withSql ? new DatabaseSync(":memory:") : null;
  const context = {
    storage: {
      get: async (key) => structuredClone(data.get(key)),
      put: async (key, value) => { data.set(key, structuredClone(value)); },
      deleteAll: async () => { data.clear(); },
      setAlarm: async () => {},
      ...(database ? { sql: { exec: (query, ...parameters) => database.prepare(query).all(...parameters) } } : {}),
    },
    getWebSockets: () => [],
  };
  const object = new ObjectType(context, env);
  let pending = Promise.resolve();
  return {
    // Mirror Durable Object input gates, preserving write-once slot races.
    fetch(request) {
      const response = pending.then(() => object.fetch(request));
      pending = response.catch(() => {});
      return response;
    },
    close: () => database?.close(),
  };
}

const env = {
  ENVIRONMENT: "development",
  CORS_ALLOWED_ORIGINS: origin,
  MAILBOX: {
    idFromName: (id) => id,
    get(id) {
      if (!rooms.has(id)) rooms.set(id, memoryObject(Mailbox));
      return rooms.get(id);
    },
  },
  MAILBOX_QUOTA: {
    idFromName: (id) => id,
    get(id) {
      if (!quotas.has(id)) quotas.set(id, memoryObject(MailboxQuota, true));
      return quotas.get(id);
    },
  },
};

async function bodyOf(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  return Buffer.concat(chunks);
}

const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json" };

createServer(async (incoming, outgoing) => {
  try {
    const url = new URL(incoming.url, origin);
    if (url.pathname === "/__test/reset" && incoming.method === "POST") {
      const settings = JSON.parse((await bodyOf(incoming)).toString() || "{}");
      rooms.clear();
      for (const quota of quotas.values()) quota.close();
      quotas.clear();
      audit = [];
      fault = settings.fault ?? null;
      resetWorkerState();
      outgoing.writeHead(204).end();
      return;
    }
    if (url.pathname === "/__test/audit") {
      outgoing.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(audit));
      return;
    }

    // A Playwright network header identifies the isolated browser context.
    // The production client correctly omits cookies on signaling requests.
    const client = Number(incoming.headers["x-sketch-test-client"] || 1);
    if (url.pathname.startsWith("/mailbox/") || url.pathname === "/turn-credentials") {
      const body = await bodyOf(incoming);
      const headers = new Headers(incoming.headers);
      // Each isolated browser context represents a different participant/IP.
      headers.set("CF-Connecting-IP", `198.51.100.${client}`);
      const request = new Request(url, {
        method: incoming.method,
        headers,
        ...(body.length ? { body } : {}),
      });
      const entry = { method: request.method, url: request.url, body: body.toString(), client };
      audit.push(entry);
      const response = await handleRequest(request, env, {
        // Never contact Cloudflare, even if a regression unexpectedly tries to.
        fetch: async () => { throw new Error("External requests forbidden in loopback tests"); },
        ...(fault ? {
          enforceAccess: async () => new Response(JSON.stringify({ error: fault === 429 ? "cap" : "unavailable" }), {
            status: fault,
            headers: { "content-type": "application/json" },
          }),
        } : {}),
      });
      entry.status = response.status;
      outgoing.writeHead(response.status, Object.fromEntries(response.headers));
      outgoing.end(Buffer.from(await response.arrayBuffer()));
      return;
    }
    if (url.pathname === "/config.js") {
      outgoing.writeHead(200, { "content-type": "text/javascript", "cache-control": "no-store" });
      outgoing.end(`export const RELAY_ORIGIN = ${JSON.stringify(origin)};\n`);
      return;
    }
    const path = resolve(root, `.${url.pathname === "/" ? "/index.html" : url.pathname}`);
    if (!path.startsWith(`${root.replace(/\/$/, "")}${sep}`)) {
      outgoing.writeHead(403).end();
      return;
    }
    const content = await readFile(path);
    outgoing.writeHead(200, { "content-type": types[extname(path)] || "application/octet-stream", "cache-control": "no-store" });
    outgoing.end(content);
  } catch (error) {
    if (error.code !== "ENOENT") console.error(error);
    outgoing.writeHead(error.code === "ENOENT" ? 404 : 500).end();
  }
}).listen(4173, "127.0.0.1");
