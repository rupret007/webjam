/**
 * Browser vs desktop request classification and CORS for the web Sketch Jam client.
 * Desktop mailbox/TURN calls send no Origin header and stay on the desktop quota path.
 */

const DEFAULT_ORIGIN_PATTERNS = [
  "https://stalemate.band",
  "http://localhost:*",
  "http://127.0.0.1:*",
];

export function corsAllowlist(env = {}) {
  const raw = String(env.WEBJAM_CORS_ORIGINS ?? "").trim();
  if (!raw) {
    return DEFAULT_ORIGIN_PATTERNS;
  }
  return raw
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean);
}

export function originAllowed(origin, env = {}) {
  const value = String(origin ?? "").trim();
  if (!value) {
    return false;
  }
  for (const pattern of corsAllowlist(env)) {
    if (pattern === value) {
      return true;
    }
    if (pattern.endsWith(":*")) {
      const prefix = pattern.slice(0, -1);
      if (value.startsWith(prefix)) {
        const rest = value.slice(prefix.length);
        if (/^\d+$/.test(rest)) {
          return true;
        }
      }
    }
  }
  return false;
}

export function requestOrigin(headers) {
  return String(headers?.get?.("Origin") ?? "").trim();
}

export function hasBrowserFetchMode(headers) {
  return Boolean(String(headers?.get?.("Sec-Fetch-Mode") ?? "").trim());
}

export function isBrowserClassifiedRequest(request) {
  if (request.method === "OPTIONS") {
    return true;
  }
  const headers = request.headers;
  if (requestOrigin(headers)) {
    return true;
  }
  return hasBrowserFetchMode(headers);
}

function corsHeaders(origin) {
  return {
    "access-control-allow-origin": origin,
    "access-control-allow-methods": "GET, POST, DELETE, OPTIONS",
    "access-control-allow-headers": "content-type",
    "access-control-max-age": "86400",
    vary: "Origin",
  };
}

export function handleCorsPreflight(request, env = {}) {
  const origin = requestOrigin(request.headers);
  if (!origin || !originAllowed(origin, env)) {
    return { blocked: true, status: 403, code: "origin" };
  }
  return {
    blocked: false,
    response: new Response(null, { status: 204, headers: corsHeaders(origin) }),
  };
}

/**
 * Returns { browser, blocked, status, code, response }.
 * blocked=true means return response or error immediately (before quota / DO).
 */
export function classifyBrowserRequest(request, env = {}) {
  if (!isBrowserClassifiedRequest(request)) {
    return { browser: false, blocked: false };
  }
  if (request.method === "OPTIONS") {
    const preflight = handleCorsPreflight(request, env);
    if (preflight.blocked) {
      return { browser: true, blocked: true, status: preflight.status, code: preflight.code };
    }
    return { browser: true, blocked: true, response: preflight.response };
  }
  const origin = requestOrigin(request.headers);
  if (!origin || !originAllowed(origin, env)) {
    return { browser: true, blocked: true, status: 403, code: "origin" };
  }
  return { browser: true, blocked: false, corsOrigin: origin };
}

export function withCors(response, corsOrigin) {
  if (!corsOrigin || !response) {
    return response;
  }
  const headers = new Headers(response.headers);
  for (const [key, value] of Object.entries(corsHeaders(corsOrigin))) {
    headers.set(key, value);
  }
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
    webSocket: response.webSocket,
  });
}
