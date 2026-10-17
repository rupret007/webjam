// Reviewed provider allocation addresses. A provider change fails closed until
// this exact allowlist is reviewed: https://developers.cloudflare.com/realtime/turn/faq/
export const RELAY_IPS = Object.freeze(["141.101.90.1", "162.159.207.1"]);
export const SESSION_BYTES = 256 * 1024 * 1024;
export function publicIPv4(value) {
  if (typeof value !== "string" || !/^(?:0|[1-9][0-9]{0,2})(?:\.(?:0|[1-9][0-9]{0,2})){3}$/.test(value)) return false;
  const [a, b, c, d] = value.split(".").map(Number);
  if ([a, b, c, d].some((part) => part > 255)) return false;
  return !(a === 0 || a === 10 || a === 127 || a >= 224 || (a === 100 && b >= 64 && b <= 127) || (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) || (a === 192 && (b === 168 || (b === 0 && (c === 0 || c === 2)) || (b === 88 && c === 99))) || (a === 198 && (b === 18 || b === 19 || (b === 51 && c === 100))) || (a === 203 && b === 0 && c === 113));
}
export function candidateAllowed(line, relays = RELAY_IPS) {
  const fields = line.replace(/^a=/, "").split(/ +/);
  if (fields.length < 8 || !fields[0].startsWith("candidate:") || fields[1] !== "1" || fields[2].toLowerCase() !== "udp" || !/^\d+$/.test(fields[3]) || !publicIPv4(fields[4]) || !/^\d+$/.test(fields[5]) || Number(fields[5]) < 1 || Number(fields[5]) > 65535 || fields[6] !== "typ") return false;
  return fields[7] === "srflx" || (fields[7] === "relay" && relays.includes(fields[4]));
}
export function policySdp(sdp, { remote = false } = {}) {
  const lines = sdp.split(/\r?\n/).filter(Boolean);
  let count = 0;
  const result = lines.filter((line) => {
    if (!line.startsWith("a=candidate:")) return true;
    const allowed = candidateAllowed(line);
    if (!allowed && remote) throw new Error("Host supplied a disallowed ICE candidate");
    if (allowed && ++count > 32) throw new Error("Too many ICE candidates");
    return allowed;
  }).map((line) => {
    // Never disclose a private related address from a server-reflexive candidate.
    if (line.startsWith("a=candidate:")) return line.replace(/ raddr [^ ]+ rport [0-9]+/, " raddr 0.0.0.0 rport 0");
    if (line.startsWith("c=IN IP")) return "c=IN IP4 0.0.0.0";
    return line;
  });
  if (!count) throw new Error("No permitted network path. Try the app or another network.");
  return `${result.join("\r\n")}\r\n`;
}
export function validateCredentials(document, now = Date.now()) {
  if (!document || document.client !== "web" || !Number.isFinite(document.ttl) || document.ttl <= 0 || document.ttl > 3600 || !Number.isSafeInteger(document.quota_bytes) || document.quota_bytes <= 0 || document.quota_bytes > SESSION_BYTES || !Array.isArray(document.iceServers) || document.iceServers.length < 1 || document.iceServers.length > 8) throw new Error("Relay busy, try same Wi-Fi or the app.");
  if (document.allowed_relay_ips !== undefined && (!Array.isArray(document.allowed_relay_ips) || document.allowed_relay_ips.some((ip) => !RELAY_IPS.includes(ip)))) throw new Error("Untrusted relay address");
  let hasTurn = false;
  for (const server of document.iceServers) {
    const urls = typeof server.urls === "string" ? [server.urls] : server.urls;
    if (!Array.isArray(urls) || urls.length < 1 || urls.length > 8) throw new Error("Invalid relay configuration");
    for (const url of urls) {
      if (!/^(?:stun:stun\.cloudflare\.com:3478|stun:turn\.cloudflare\.com:3478|turn:turn\.cloudflare\.com:(?:3478|80|443)\?transport=(?:udp|tcp)|turns:turn\.cloudflare\.com:(?:5349|443)\?transport=tcp)$/.test(url)) throw new Error("Untrusted relay configuration");
      if (url.startsWith("turn")) {
        hasTurn = true;
        if (typeof server.username !== "string" || !server.username || typeof server.credential !== "string" || !server.credential || server.username.length > 512 || server.credential.length > 512) throw new Error("Invalid relay credential");
      }
    }
  }
  if (!hasTurn) throw new Error("Relay busy, try same Wi-Fi or the app.");
  return { iceServers: document.iceServers, expiresAt: now + document.ttl * 1000, quotaBytes: document.quota_bytes };
}
