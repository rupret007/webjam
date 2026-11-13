// Guest side of the host-created webjoin-game-offer channel.
// The one-time value is held only long enough to open a new tab, then dropped.

export const GAME_OFFER_LABEL = "webjoin-game-offer";
export const OFFER_TTL_MS = 120000;
export const AGE_LINE = "By joining, you confirm you're 13+ and agree to the house rules.";
const HANGOUT_GUEST_PAGE = "https://stalemate-hangout-beta.jeffstory007.workers.dev/beta/game-night/webjam-guest";
const FULL_COPY = "Table full, you're up next game";
const EXPIRED_COPY = "This game offer expired";

let generation = 0;
let pendingTimer = 0;
let cancelTimer = (id) => clearTimeout(id);

function defaultRoot() {
  if (typeof document === "undefined" || typeof document.getElementById !== "function") return null;
  return document.getElementById("game-offer");
}

function validHostName(name) {
  if (typeof name !== "string" || name.length === 0 || name.length > 40 || name.trim() === "") return false;
  return /^[A-Za-z0-9 .'-]+$/.test(name);
}

function tokenBytes(token) {
  if (typeof token !== "string" || token.length < 22 || token.length > 43) return 0;
  if (!/^[A-Za-z0-9_-]+$/.test(token) || token.length % 4 === 1) return 0;
  try {
    const padded = token + "=".repeat((4 - (token.length % 4)) % 4);
    const binary = atob(padded.replace(/-/g, "+").replace(/_/g, "/"));
    return binary.length >= 16 && binary.length <= 32 ? binary.length : 0;
  } catch {
    return 0;
  }
}

export function classifyGameOffer(data) {
  if (typeof data !== "string" || data.length === 0 || data.length > 128) return null;
  let body;
  try { body = JSON.parse(data); } catch { return null; }
  if (!body || typeof body !== "object" || Array.isArray(body)) return null;
  const keys = Object.keys(body).sort().join(",");
  if (body.game !== "chess" || !validHostName(body.hostName)) return null;
  if (keys === "game,hostName,value" && tokenBytes(body.value) >= 16) {
    return { kind: "join", hostName: body.hostName, value: body.value };
  }
  if (keys === "game,hostName,state" && (body.state === "full" || body.state === "expired")) {
    return { kind: "status", state: body.state, hostName: body.hostName };
  }
  return null;
}

export function guestGameURL(value) {
  if (tokenBytes(value) < 16) return "";
  let url;
  try { url = new URL(HANGOUT_GUEST_PAGE); } catch { return ""; }
  if (url.search || url.username || url.password || url.hash) return "";
  url.hash = `grant=${value}&wait=1`;
  const href = url.href;
  const mark = href.indexOf("#");
  if (mark < 0) return "";
  const before = href.slice(0, mark);
  if (before.includes("?") || before.includes(value)) return "";
  if (href.slice(mark + 1) !== `grant=${value}&wait=1`) return "";
  return href;
}

export function openGameWindow(url, target = "_blank", features = "noopener,noreferrer") {
  if (typeof window === "undefined" || typeof window.open !== "function") return null;
  return window.open(url, target, features);
}

function beginCard() {
  generation += 1;
  if (pendingTimer) {
    cancelTimer(pendingTimer);
    pendingTimer = 0;
  }
  return generation;
}

function mount(root, doc, nodes) {
  root.hidden = false;
  root.textContent = "";
  const card = doc.createElement("section");
  card.className = "game-offer";
  for (const node of nodes) card.appendChild(node);
  root.appendChild(card);
  return card;
}

function leaf(doc, tag, text) {
  const node = doc.createElement(tag);
  node.textContent = text;
  return node;
}

function showStatus(root, doc, state, env) {
  const copy = state === "full" ? FULL_COPY : state === "expired" ? EXPIRED_COPY : "";
  if (!copy) return;
  beginCard();
  const button = leaf(doc, "button", "OK");
  button.type = "button";
  button.disabled = false;
  button.addEventListener("click", (event) => {
    event?.preventDefault?.();
    clearGameOffer(root);
  });
  mount(root, doc, [leaf(doc, "p", copy), button]);
  env?.onCard?.({ state, copy });
}

function showJoin(root, doc, offer, env) {
  const ticket = beginCard();
  const value = offer.value;
  const button = leaf(doc, "button", "Join game");
  button.type = "button";
  button.disabled = false;
  button.setAttribute?.("aria-describedby", "game-offer-age");
  const age = leaf(doc, "p", AGE_LINE);
  age.id = "game-offer-age";
  button.addEventListener("click", (event) => {
    event?.preventDefault?.();
    if (ticket !== generation) return;
    const href = guestGameURL(value);
    if (!href) return;
    const open = env.openWindow || openGameWindow;
    open(href, "_blank", "noopener,noreferrer");
  });
  mount(root, doc, [leaf(doc, "p", `${offer.hostName} started Chess — Join game`), age, button]);
  const schedule = env.schedule || setTimeout;
  cancelTimer = env.cancel || ((id) => clearTimeout(id));
  pendingTimer = schedule(() => {
    pendingTimer = 0;
    if (ticket !== generation) return;
    showStatus(root, doc, "expired", env);
  }, OFFER_TTL_MS);
}

export function clearGameOffer(root) {
  beginCard();
  const target = root || defaultRoot();
  if (!target) return;
  target.hidden = true;
  target.textContent = "";
}

export function bindGameOfferChannel(channel, env = {}) {
  if (!channel || channel.label !== GAME_OFFER_LABEL || channel.ordered === false || channel.maxRetransmits != null || channel.maxPacketLifeTime != null) {
    try { channel?.close?.(); } catch { /* The peer may already be closed. */ }
    return false;
  }
  const authorized = typeof env.authorized === "function" ? env.authorized : () => false;
  channel.addEventListener("message", (event) => {
    if (authorized() !== true) return;
    const message = classifyGameOffer(event?.data);
    if (!message) return;
    const root = env.root || defaultRoot();
    const doc = root?.ownerDocument || env.document;
    if (!root || typeof doc?.createElement !== "function") return;
    if (message.kind === "join") showJoin(root, doc, message, env);
    else showStatus(root, doc, message.state, env);
  });
  return true;
}
