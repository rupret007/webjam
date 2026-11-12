import { test, expect } from "@playwright/test";
import { toBase64Url, fromHex } from "../../crypto.js";

function privateTestFragment() {
  const capability = toBase64Url(fromHex("11".repeat(32)));
  const session = "22".repeat(32);
  const expiresAt = Math.floor(Date.now() / 1000) + 300;
  return `#k=${capability}&s=${session}&g=1&e=${expiresAt}`;
}

test("keyboard skip continues to headphones, then Join", async ({ page, browserName }) => {
  await page.setViewportSize({ width: 390, height: 667 });
  await page.goto(`/${privateTestFragment()}`);
  await page.getByRole("button", { name: "Skip check", exact: true }).press("Enter");
  await expect(page.getByRole("checkbox")).toBeFocused();
  await page.keyboard.press("Space");
  await page.keyboard.press(process.platform === "darwin" && browserName === "webkit" ? "Alt+Tab" : "Tab");
  await expect(page.getByRole("button", { name: "Join with microphone" })).toBeFocused();
});

test("keyboard skip focuses Join when headphones are already confirmed", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 667 });
  await page.goto(`/${privateTestFragment()}`);
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Skip check", exact: true }).press("Enter");
  await expect(page.getByRole("button", { name: "Join with microphone" })).toBeFocused();
});

test("a microphone failure keeps focus where the guest moved it", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(Object.getPrototypeOf(navigator.mediaDevices), "getUserMedia", {
      configurable: true,
      value: () => new Promise((resolve, reject) => {
        window.failAudioCheck = () => reject(new DOMException("No test input", "NotFoundError"));
      }),
    });
  });
  await page.goto(`/${privateTestFragment()}`);
  await page.getByRole("button", { name: "Check microphone" }).press("Enter");
  await expect(page.locator("#mic-progress")).toBeFocused();
  await page.waitForFunction(() => typeof window.failAudioCheck === "function");
  const headphones = page.getByRole("checkbox");
  await headphones.focus();
  await page.evaluate(() => window.failAudioCheck());
  await expect(page.getByRole("button", { name: "Try again", exact: true })).toBeVisible();
  await expect(headphones).toBeFocused();
});
