import { expect, test } from "@playwright/test";
import { fromHex, toBase64Url } from "../../crypto.js";

test.use({ isMobile: true, hasTouch: true });

for (const width of [375, 390]) {
  test(`microphone picker has a full-size touch target at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.addInitScript(() => {
      const media = Object.getPrototypeOf(navigator.mediaDevices);
      media.enumerateDevices = async () => [
        { kind: "audioinput", deviceId: "headset", label: "USB headset" },
      ];
      media.getUserMedia = async () => {
        throw new DOMException("Could not start audio source", "NotReadableError");
      };
    });
    const expiry = Math.floor(Date.now() / 1000) + 120;
    const capability = toBase64Url(fromHex("11".repeat(32)));
    await page.goto(`/#k=${capability}&s=${"22".repeat(32)}&g=1&e=${expiry}`);
    await page.getByRole("button", { name: "Check microphone" }).tap();
    const picker = page.getByLabel("Microphone", { exact: true });
    await expect(picker).toBeVisible();
    await expect(picker.locator("option")).toHaveText(["Browser default", "USB headset"]);
    expect((await picker.boundingBox()).height).toBeGreaterThanOrEqual(44);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  });
}
