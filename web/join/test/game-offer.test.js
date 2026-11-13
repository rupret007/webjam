import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  AGE_LINE,
  GAME_OFFER_LABEL,
  OFFER_TTL_MS,
  bindGameOfferChannel,
  classifyGameOffer,
  clearGameOffer,
  guestGameURL,
} from "../game-offer.js";

const token = Buffer.alloc(32, 7).toString("base64url");
const offer = JSON.stringify({ game: "chess", hostName: "Ada", value: token });

function fakeElement(doc, tag) {
  const node = {
    tag,
    hidden: false,
    disabled: false,
    type: "",
    id: "",
    className: "",
    children: [],
    listeners: {},
    ownerDocument: doc,
    _text: "",
    set textContent(value) {
      this._text = String(value);
      this.children = [];
    },
    get textContent() {
      if (this.children.length) return this.children.map((child) => child.textContent).join("");
      return this._text;
    },
    setAttribute(name, value) { this[name] = value; },
    appendChild(child) { this.children.push(child); return child; },
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
    remove() { this.removed = true; },
  };
  return node;
}

function fakeDocument() {
  const doc = { createElement(tag) { return fakeElement(doc, tag); } };
  return { doc, root: fakeElement(doc, "section") };
}

function fakeChannel(label = GAME_OFFER_LABEL) {
  const listeners = {};
  return {
    label,
    ordered: true,
    maxRetransmits: null,
    maxPacketLifeTime: null,
    closed: false,
    close() { this.closed = true; },
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    receive(data) { for (const fn of listeners.message || []) fn({ data }); },
  };
}

function click(node) {
  const button = node.children?.[0]?.children?.find((child) => child.tag === "button") || node;
  for (const fn of button.listeners?.click || []) fn({ preventDefault() {} });
  return button;
}

function harness({ authorized = true } = {}) {
  const { root } = fakeDocument();
  const opened = [];
  const scheduled = [];
  const storage = [];
  const consoleLines = [];
  const channel = fakeChannel();
  const location = { href: "https://join.example/join/", assigned: false, assign() { this.assigned = true; } };
  globalThis.localStorage = { setItem: (...args) => storage.push(["local", ...args]), getItem: () => storage.push(["local-get"]) };
  globalThis.sessionStorage = { setItem: (...args) => storage.push(["session", ...args]) };
  const log = console.log;
  console.log = (...args) => { consoleLines.push(args.map(String).join(" ")); };
  bindGameOfferChannel(channel, {
    root,
    authorized: () => authorized,
    openWindow(url, target, features) { opened.push({ url, target, features }); return { closed: false }; },
    schedule(fn, ms) { scheduled.push({ fn, ms }); return scheduled.length; },
    cancel(id) { if (scheduled[id - 1]) scheduled[id - 1].cancelled = true; },
  });
  return {
    root, opened, scheduled, storage, consoleLines, channel, location,
    restore() { console.log = log; delete globalThis.localStorage; delete globalThis.sessionStorage; clearGameOffer(root); },
  };
}

test("game offers are accepted only on webjoin-game-offer after authorized", () => {
  const early = harness({ authorized: false });
  early.channel.receive(offer);
  assert.equal(early.root.textContent, "");
  assert.equal(early.opened.length, 0);
  early.restore();

  const guest = harness();
  guest.channel.receive(offer);
  assert.match(guest.root.textContent, /Ada started Chess — Join game/);
  assert.match(guest.root.textContent, new RegExp(AGE_LINE.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.equal(guest.root.textContent.includes(token), false);
  const button = click(guest.root);
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, "Join game");
  assert.equal(guest.opened.length, 1);
  const opened = new URL(guest.opened[0].url);
  assert.equal(opened.search, "");
  assert.equal(opened.pathname, "/beta/game-night/webjam-guest");
  assert.equal(opened.hash, `#grant=${token}&wait=1`);
  assert.equal(guest.opened[0].target, "_blank");
  assert.match(guest.opened[0].features, /noopener/);
  assert.match(guest.opened[0].features, /noreferrer/);
  assert.equal(guest.location.assigned, false);
  assert.equal(guest.location.href, "https://join.example/join/");
  assert.equal(guest.storage.length, 0);
  assert.equal(guest.consoleLines.some((line) => line.includes(token)), false);
  assert.equal(guest.scheduled[0].ms, OFFER_TTL_MS);
  guest.restore();
});

test("game offers on the enrollment channel are rejected", () => {
  const enrollment = fakeChannel("webjoin-enrollment");
  const { root } = fakeDocument();
  const opened = [];
  assert.equal(bindGameOfferChannel(enrollment, {
    root,
    authorized: () => true,
    openWindow(url) { opened.push(url); },
  }), false);
  assert.equal(enrollment.closed, true);
  assert.equal(classifyGameOffer(offer).kind, "join");
  assert.equal(root.textContent, "");
  assert.equal(opened.length, 0);
  clearGameOffer(root);
});

test("over-capacity and expired offers stay actionable and carry no value", () => {
  const guest = harness();
  guest.channel.receive(JSON.stringify({ game: "chess", hostName: "Ada", state: "full" }));
  assert.match(guest.root.textContent, /Table full, you're up next game/);
  assert.equal(guest.root.textContent.includes("Join game"), false);
  const fullButton = click(guest.root);
  assert.equal(fullButton.disabled, false);
  assert.equal(guest.opened.length, 0);
  assert.equal(guest.root.hidden, true);

  guest.channel.receive(offer);
  const joinButton = guest.root.children[0].children.find((child) => child.tag === "button");
  guest.scheduled.at(-1).fn();
  assert.match(guest.root.textContent, /This game offer expired/);
  for (const fn of joinButton.listeners.click) fn({ preventDefault() {} });
  const expiredButton = click(guest.root);
  assert.equal(expiredButton.disabled, false);
  assert.equal(guest.opened.length, 0);
  assert.equal(guest.storage.length, 0);
  guest.restore();
});

test("malformed, oversize, and early offers never open or store the value", () => {
  const guest = harness();
  const junk = [
    "{",
    JSON.stringify({ game: "chess", hostName: "Ada", value: token, url: "https://evil.example/" }),
    JSON.stringify({ game: "rummy", hostName: "Ada", value: token }),
    JSON.stringify({ game: "chess", hostName: "Ada", value: "short" }),
    "x".repeat(129),
    JSON.stringify({ game: "chess", hostName: "Ada", state: "later" }),
  ];
  for (const data of junk) {
    assert.equal(classifyGameOffer(data), null);
    guest.channel.receive(data);
  }
  assert.equal(guest.root.textContent, "");
  assert.equal(guest.opened.length, 0);
  assert.equal(guestGameURL(token).includes("?"), false);
  assert.equal(guestGameURL("short"), "");
  guest.restore();
});

test("session keeps enrollment byte-identical and wires the game-offer channel", async () => {
  const source = await readFile(new URL("../session.js", import.meta.url), "utf8");
  const offerAt = source.indexOf('channel.label === "webjoin-game-offer"');
  const enrollAt = source.indexOf('channel.label !== "webjoin-enrollment"');
  assert.ok(offerAt > 0 && enrollAt > offerAt);
  assert.match(source, /message\.data !== "authorized"/);
  assert.equal(source.includes("localStorage"), false);
  assert.equal(source.includes("sessionStorage"), false);
  const guest = await readFile(new URL("../game-offer.js", import.meta.url), "utf8");
  for (const word of ["localStorage", "sessionStorage", "console.", "location.href", "location.assign", "location.replace", "document.cookie"]) {
    assert.equal(guest.includes(word), false, word);
  }
});
