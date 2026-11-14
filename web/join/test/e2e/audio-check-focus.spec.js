import { test, expect } from "@playwright/test";
import { toBase64Url, fromHex } from "../../crypto.js";

function privateTestFragment() {
  const capability = toBase64Url(fromHex("11".repeat(32)));
  const session = "22".repeat(32);
  const expiresAt = Math.floor(Date.now() / 1000) + 300;
  return `#k=${capability}&s=${session}&g=1&e=${expiresAt}`;
}

for (const size of [{width:390,height:844},{width:1440,height:900}]) {
  test(`keyboard headphone confirmation continues to Join at ${size.width}px`, async ({ page }, info) => {
    await page.setViewportSize(size);
    await page.goto(`/${privateTestFragment()}`);
    const headphones = page.getByRole("checkbox");
    const join = page.getByRole("button", { name: "Join with microphone" });
    await page.getByRole("button", { name: "Skip check", exact: true }).press("Enter");
    await expect(headphones).toBeFocused();
    await expect(join).toBeDisabled();
    await page.screenshot({ path: info.outputPath("keyboard-before-headphones.png") });
    await page.keyboard.press("Space");
    await expect(headphones).toBeChecked();
    await expect(join).toBeFocused();
    await expect(join).toBeInViewport();
    await page.screenshot({ path: info.outputPath("keyboard-ready-to-join.png") });
    await headphones.focus();
    await page.keyboard.press("Space");
    await expect(headphones).toBeFocused();
    await expect(join).toBeDisabled();
  });
}

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
