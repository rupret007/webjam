import { test, expect } from "@playwright/test";
import { toBase64Url, fromHex } from "../../crypto.js";

const fragment = () => {
  const value = {
    capability: fromHex("11".repeat(32)),
    session: "33".repeat(32),
    generation: 1,
    expiresAt: Math.floor(Date.now() / 1000) + 300,
  };
  return `/#k=${toBase64Url(value.capability)}&s=${value.session}&g=${value.generation}&e=${value.expiresAt}`;
};

function roomSession(sessionName) {
  const assign = sessionName ? `this.sessionName = ${JSON.stringify(sessionName)};` : "";
  const line = sessionName ? `You're listening. ${sessionName}` : "You're listening.";
  return `export class WebJoinSession {
    constructor(options) { this.options = options; ${assign} }
    async join() {
      this.accessExpiresAt = Date.now() + 300000;
      this.options.onStatus(${JSON.stringify(line)});
    }
    async attachMicrophone() { return true; }
    close() {}
  }`;
}

async function openRoom(page, sessionName) {
  await page.route("**/session.js", (route) => route.fulfill({
    contentType: "text/javascript",
    body: roomSession(sessionName),
  }));
  await page.addInitScript(() => {
    const native = window.AudioContext;
    Object.defineProperty(Object.getPrototypeOf(navigator.mediaDevices), "getUserMedia", {
      configurable: true,
      value: async () => {
        const context = new native();
        await context.resume();
        return context.createMediaStreamDestination().stream;
      },
    });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(fragment());
}

test("the first in-room line is You're listening and the delay and countdown sit under Audio details", async ({ page }) => {
  await openRoom(page);
  await expect(page.locator("#heading")).toHaveText("You're listening.");
  await expect(page.locator("#status")).toHaveText("You're listening.");
  await expect(page.locator("#status")).not.toContainText("60–120");
  const delayNote = page.getByText("roughly 60–120 ms", { exact: false });
  await expect(delayNote).toBeHidden();
  await expect(page.locator("#access-window")).toBeHidden();
  await expect(page.locator("#listen-note")).toContainText("You are listening.");
  await page.getByText("Audio details", { exact: true }).click();
  await expect(delayNote).toBeVisible();
  await expect(page.locator("#access-time")).toContainText("Browser access ends in");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("a known session name is part of the first in-room line", async ({ page }) => {
  await openRoom(page, "Friday set");
  await expect(page.locator("#heading")).toHaveText("You're listening. Friday set");
  await expect(page.locator("#status")).toHaveText("You're listening. Friday set");
});

test("Use microphone turns a silent mic on without waiting for a level", async ({ page }) => {
  await openRoom(page);
  await expect(page.locator("#listen-note")).toContainText("You are listening.");
  await page.getByRole("button", { name: "Use microphone", exact: true }).click();
  await expect(page.locator("#mic-heard")).toHaveText("Microphone on.");
  await expect(page.locator("#listen-note")).toBeHidden();
  await expect(page.getByText("You are listening.", { exact: true })).toHaveCount(0);
  await expect(page.locator("#mic-progress")).not.toHaveText(/Speak or play a note/);
  await expect(page.locator("#heading")).toHaveText("You're listening.");
  await expect(page.locator("#join-guidance")).toHaveText("Microphone on. Headphones prevent echo and feedback.");
});
