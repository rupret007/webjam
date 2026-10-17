import { test, expect } from "@playwright/test";
import { installFakeAudio } from "./fake-audio.js";
import { createInvite, inviteFragment } from "../../crypto.js";

let nextParticipant = 0;

test.beforeEach(async ({ request }) => {
  nextParticipant = 0;
  await request.post("/__test/reset", { data: {} });
});

async function participant(browser, name, invite = "/", { fakeAudioOnly = false } = {}) {
  const nativeMockCapture = !fakeAudioOnly && browser.browserType().name() === "webkit";
  // Safari mutes an older tab's microphone when a second tab starts capture.
  // Independent WebKit processes model independent people with live inputs.
  const isolatedBrowser = nativeMockCapture ? await browser.browserType().launch() : null;
  let context;
  const close = async () => {
    try {
      await context?.close();
    } finally {
      await isolatedBrowser?.close();
    }
  };
  try {
    context = await (isolatedBrowser ?? browser).newContext({
      baseURL: "http://127.0.0.1:4173",
      permissions: nativeMockCapture ? ["microphone"] : [],
      extraHTTPHeaders: { "x-sketch-test-client": String(++nextParticipant) },
    });
    await context.route(/^https?:\/\//, (route) => {
      if (new URL(route.request().url()).hostname !== "127.0.0.1") return route.abort();
      return route.continue();
    });
    await installFakeAudio(context, { nativeMockCapture });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => {
      if (/Content Security Policy|Refused to/.test(message.text())) errors.push(message.text());
    });
    await page.goto(invite);
    await page.locator("#name").fill(name);
    return { page, errors, close };
  } catch (error) {
    await close();
    throw error;
  }
}

async function join(page) {
  await page.locator("#headphones").check();
  await page.locator("#join").click();
}

test("Sketch Jam label and headphones consent precede microphone capture", async ({ browser, request }) => {
  const { close, page } = await participant(browser, "Headphones");
  try {
    await expect(page.getByRole("heading", { name: "Sketch Jam", exact: true })).toBeVisible();
    await expect(page.getByText("Wear headphones. Echo cancelling is off for music quality.", { exact: false })).toBeVisible();
    await expect(page.getByText(/Great for ideas and loose jams/)).toBeVisible();
    await expect(page.locator("#join")).toBeDisabled();
    expect(await page.evaluate(() => window.__captureCalls)).toEqual([]);
    expect(await (await request.get("/__test/audit")).json()).toEqual([]);
    await join(page);
    await expect.poll(() => page.evaluate(() => window.__captureCalls.length)).toBe(1);
    expect(await page.evaluate(() => window.__captureCalls[0])).toEqual({
      video: false,
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 2, sampleRate: 48000 },
    });
  } finally {
    await close();
  }
});

test("same-tab hashchange reloads the invite and re-enables join", async ({ browser }) => {
  const { close, page } = await participant(browser, "Navigator");
  try {
    const first = await page.locator("#invite").inputValue();
    const nextHash = `#${inviteFragment(createInvite())}`;
    await page.evaluate((hash) => {
      location.hash = hash;
    }, nextHash);
    await expect.poll(() => page.locator("#invite").inputValue()).not.toBe(first);
    await expect(page.locator("#invite")).toHaveValue(new RegExp(`${nextHash.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`));
    await page.locator("#headphones").check();
    await expect(page.locator("#join")).toBeEnabled();
  } finally {
    await close();
  }
});

test("a malformed invite cannot capture audio or contact the mailbox", async ({ browser, request }) => {
  const { close, page } = await participant(browser, "Invalid invite", "/#k=invalid&s=invalid");
  try {
    await expect(page.locator("#status")).toContainText(/Invalid invite/i);
    await page.locator("#headphones").check();
    await expect(page.locator("#join")).toBeDisabled();
    expect(await page.evaluate(() => window.__captureCalls)).toEqual([]);
    expect(await (await request.get("/__test/audit")).json()).toEqual([]);
  } finally {
    await close();
  }
});

for (const count of [2, 3, 4]) {
  test(`${count} peers exchange real audio through an encrypted loopback mailbox`, async ({ browser, request }) => {
    const participants = [];
    try {
      const host = await participant(browser, "Player 1");
      participants.push(host);
      await join(host.page);
      await expect(host.page.locator("#invite")).toHaveValue(/#.*k=/);
      const invite = await host.page.locator("#invite").inputValue();
      const secret = new URL(invite).hash.match(/(?:#|&)k=([^&]+)/)[1];
      for (let index = 1; index < count; index += 1) {
        const peer = await participant(browser, `Player ${index + 1}`, invite);
        participants.push(peer);
        await join(peer.page);
      }
      for (const { page } of participants) {
        await expect(page.locator("#peers [data-state='connected']")).toHaveCount(count - 1, { timeout: 210_000 });
        await expect.poll(async () => page.evaluate(async () => {
          const received = await Promise.all(window.__connections.map(async (pc) => {
            const stats = await pc.getStats();
            return [...stats.values()].some((stat) => stat.type === "inbound-rtp" && (stat.kind === "audio" || stat.mediaType === "audio") && stat.bytesReceived > 0);
          }));
          return received.filter(Boolean).length;
        }), { timeout: 30_000 }).toBe(count - 1);
        expect(await page.evaluate(() => window.__connections.every((pc) => pc.getSenders().filter((sender) => sender.track?.kind === "audio").length === 1))).toBe(true);
      }
      const audit = await (await request.get("/__test/audit")).json();
      const posts = audit.filter((entry) => entry.method === "POST" && entry.url.includes("/mailbox/"));
      expect(posts.length).toBeGreaterThanOrEqual(count * (count - 1));
      for (const entry of posts) {
        expect(entry.url).toMatch(/\/mailbox\/[a-f0-9]{64}\/(offer|answer)(\?|$)/);
        const body = JSON.parse(entry.body);
        expect(Object.keys(body)).toEqual(["payload"]);
        expect(body.payload).toMatch(/^[A-Za-z0-9_-]+$/);
        expect(body.payload.length).toBeLessThanOrEqual(4096);
      }
      expect(JSON.stringify(audit)).not.toContain(secret);
      expect(JSON.stringify(audit)).not.toContain("a=fingerprint");
      expect(JSON.stringify(audit)).not.toContain("Player 1");
      if (count === 4) {
        const extra = await participant(browser, "Player 5", invite);
        participants.push(extra);
        await join(extra.page);
        await expect(extra.page.locator("#status")).toContainText("This Sketch Jam is full (4 people). Create a new link.");
        expect(await extra.page.evaluate(() => window.__connections.length)).toBe(0);
        await expect.poll(() => extra.page.evaluate(() => window.__captureTracks.every((track) => track.readyState === "ended"))).toBe(true);
      }
      for (const { page } of participants.slice(0, count)) {
        await page.locator("#leave").click();
        await expect.poll(() => page.evaluate(() => window.__captureTracks.every((track) => track.readyState === "ended"))).toBe(true);
        await expect.poll(() => page.evaluate(() => window.__connections.every((pc) => pc.connectionState === "closed"))).toBe(true);
      }
      for (const participant of participants) expect(participant.errors).toEqual([]);
    } finally {
      await Promise.all(participants.map(({ close }) => close()));
    }
  });
}

for (const status of [429, 503]) {
  test(`relay ${status} fails closed with actionable copy and releases capture`, async ({ browser, request }) => {
    await request.post("/__test/reset", { data: { fault: status } });
    const { close, page } = await participant(browser, "Busy relay");
    try {
      await join(page);
      await expect(page.locator("#status")).toContainText(/relay busy, try same Wi-Fi or the app/i, { timeout: 30_000 });
      await expect.poll(() => page.evaluate(() => window.__captureTracks.every((track) => track.readyState === "ended"))).toBe(true);
      expect(await page.evaluate(() => localStorage.length)).toBe(0);
    } finally {
      await close();
    }
  });
}

test.describe("microphone permission denial or pre-claim network failure allows retrying Join on the same invite link", () => {
  test.beforeEach(({ browserName }) => {
    test.skip(browserName === "webkit", "WebKit headless reports a native capture error instead of app join status");
  });

  test("after microphone denial", async ({ browser }) => {
    const { close, page } = await participant(browser, "Denied once", "/", { fakeAudioOnly: true });
    try {
      await page.evaluate(() => {
        let denied = true;
        const mediaPrototype = Object.getPrototypeOf(navigator.mediaDevices);
        const prior = mediaPrototype.getUserMedia;
        Object.defineProperty(mediaPrototype, "getUserMedia", {
          configurable: true,
          value: async (constraints) => {
            if (denied) {
              denied = false;
              throw new DOMException("Permission denied", "NotAllowedError");
            }
            return prior.call(navigator.mediaDevices, constraints);
          },
        });
      });
      await page.locator("#headphones").check();
      await page.locator("#join").click();
      await expect(page.locator("#status")).toContainText(/Microphone access is needed/i);
      await expect(page.locator("#join")).toBeEnabled();
      await page.locator("#join").click();
      await expect(page.locator("#status")).toContainText(/Joining with encrypted signaling|Joined/i, { timeout: 30_000 });
    } finally {
      await close();
    }
  });

  test("after relay outage before seat claim", async ({ browser, request }) => {
    await request.post("/__test/reset", { data: { fault: 503 } });
    const { close, page } = await participant(browser, "Busy relay", "/", { fakeAudioOnly: true });
    try {
      await page.locator("#headphones").check();
      await page.locator("#join").click();
      await expect(page.locator("#status")).toContainText(/relay busy/i, { timeout: 30_000 });
      await expect(page.locator("#join")).toBeEnabled();
    } finally {
      await close();
    }
  });
});
