// Exact origins only. Preview and localhost ports must each be configured;
// localhost is additionally gated by an explicit development environment.
const DEFAULT_ORIGIN = "https://stalemate.band";
const METHODS = new Set(["GET", "POST", "DELETE"]);
const HEADERS = new Set(["content-type"]);

function configuredOrigin(value, development) {
  if (value.includes("*")) return false;
  try {
    const url = new URL(value);
    if (url.origin !== value || url.username || url.password) return false;
    if (url.protocol === "https:") return true;
    return development && url.protocol === "http:" &&
      (url.hostname === "localhost" || url.hostname === "127.0.0.1");
  } catch {
    return false;
  }
}

export function allowedOrigin(request, env = {}) {
  const origin = request.headers.get("Origin");
  if (origin === null) return null;
  const development = env.ENVIRONMENT === "development";
  const origins = String(env.CORS_ALLOWED_ORIGINS ?? DEFAULT_ORIGIN)
    .split(",").map((value) => value.trim())
    .filter((value) => configuredOrigin(value, development));
  return origins.includes(origin) ? origin : false;
}

export function corsGate(request, env) {
  const origin = allowedOrigin(request, env);
  if (origin === false) return new Response(null, { status: 403 });
  if (request.method !== "OPTIONS") return null;
  const method = request.headers.get("Access-Control-Request-Method");
  const headers = String(request.headers.get("Access-Control-Request-Headers") ?? "")
    .split(",").map((value) => value.trim().toLowerCase()).filter(Boolean);
  if (!origin || !METHODS.has(method) || headers.some((value) => !HEADERS.has(value))) {
    return new Response(null, { status: 403 });
  }
  return new Response(null, {
    status: 204,
    headers: {
      "Access-Control-Allow-Origin": origin,
      "Access-Control-Allow-Methods": "GET, POST, DELETE",
      "Access-Control-Allow-Headers": "Content-Type",
      "Access-Control-Max-Age": "600",
      "Vary": "Origin, Access-Control-Request-Method, Access-Control-Request-Headers",
      "Cache-Control": "no-store",
    },
  });
}

export function withCors(response, origin) {
  if (!origin) return response;
  if (response.status === 101) return response;
  const headers = new Headers(response.headers);
  headers.set("Access-Control-Allow-Origin", origin);
  const vary = String(headers.get("Vary") ?? "").split(",").map((value) => value.trim()).filter(Boolean);
  if (!vary.some((value) => value.toLowerCase() === "origin")) vary.push("Origin");
  headers.set("Vary", vary.join(", "));
  // Cloudflare's Response extension retains the accepted WebSocket on 101.
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
    ...(response.webSocket ? { webSocket: response.webSocket } : {}),
  });
}
