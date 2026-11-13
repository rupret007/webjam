import { test, expect } from "@playwright/test";
import { toBase64Url, fromHex } from "../../crypto.js";

const invite = () => ({
  capability: fromHex("11".repeat(32)),
  session: "22".repeat(32),
  generation: 1,
  expiresAt: Math.floor(Date.now() / 1000) + 300,
});
const fragment = (value) =>
  `#k=${toBase64Url(value.capability)}&s=${value.session}&g=${value.generation}&e=${value.expiresAt}`;

async function connected(page) {
  await page.route("**/session.js", (route) => route.fulfill({
    contentType: "text/javascript",
    body: `export class WebJoinSession {
      constructor(options) { this.options = options; }
      async join() { this.options.onStatus("Synthetic session connected."); }
      async attachMicrophone() { return true; }
      close() {}
    }`,
  }));
}

test("Join is on screen without a microphone or headphone step", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/${fragment(invite())}`);
  const join = page.getByRole("button", { name: "Join", exact: true });
  await expect(join).toBeEnabled();
  await expect(join).toBeInViewport({ ratio: 1 });
  await expect(page.locator("#prejoin")).toBeHidden();
  await expect(page.getByRole("checkbox")).toBeHidden();
  await expect(page.getByRole("button", { name: "Use microphone" })).toBeHidden();
  await expect(page.locator("#join-guidance")).toHaveText(
    "Join to listen. You do not need a microphone or headphones yet.",
  );
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("optional microphone stays after Join and a missing device can be retried", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => {
    const prototype = Object.getPrototypeOf(navigator.mediaDevices);
    prototype.enumerateDevices = async () => [{ kind: "audioinput", deviceId: "fake", label: "Fake" }];
    prototype.getUserMedia = async () => {
      throw new DOMException("Requested device not found", "NotFoundError");
    };
  });
  await connected(page);
  await page.goto(`/${fragment(invite())}`);
  await page.getByRole("button", { name: "Join", exact: true }).click();
  await expect(page.locator("#status")).toHaveText("Synthetic session connected.");
  await expect(page.locator("#prejoin")).toBeVisible();
  await page.getByRole("button", { name: "Use microphone" }).click();
  await expect(page.getByText("No microphone found - plug one in and press Try again")).toBeVisible();
  await expect(page.locator("#status")).toHaveText("Synthetic session connected.");
  const retry = page.getByRole("button", { name: "Try again", exact: true });
  await expect(retry).toBeVisible();
  await retry.click();
  await expect(page.getByText("No microphone found - plug one in and press Try again")).toBeVisible();
});

test("optional headphones guidance is visible after Join and does not gate listening", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await connected(page);
  await page.goto(`/${fragment(invite())}`);
  const join = page.getByRole("button", { name: "Join", exact: true });
  await expect(join).toBeEnabled();
  await join.click();
  await expect(page.getByRole("checkbox", { name: /headphones/i })).toBeVisible();
  await expect(page.locator("#join-guidance")).toContainText("Headphones prevent echo");
  await page.getByRole("checkbox", { name: /headphones/i }).check();
  await expect(page.locator("#status")).toHaveText("Synthetic session connected.");
  await expect(page.locator("#join")).toBeHidden();
});
