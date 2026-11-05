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

test("fake mic level enables Join after headphones confirmation", async ({ page }) => {
  await page.addInitScript(() => {
    const prototype = Object.getPrototypeOf(navigator.mediaDevices);
    const nativeCapture = prototype.getUserMedia;
    Object.defineProperty(prototype, "getUserMedia", {
      configurable: true,
      value: async (constraints) => {
        const stream = await nativeCapture.call(navigator.mediaDevices, constraints);
        const context = new AudioContext({ sampleRate: 48000 });
        await context.resume();
        const analyser = context.createAnalyser();
        context.createMediaStreamSource(stream).connect(analyser);
        const samples = new Float32Array(analyser.fftSize);
        analyser.getFloatTimeDomainData(samples);
        let energy = 0;
        for (const value of samples) energy += value * value;
        if (Math.sqrt(energy / samples.length) > 0.001) return stream;
        stream.getTracks().forEach((track) => track.stop());
        const tone = new AudioContext({ sampleRate: 48000 });
        await tone.resume();
        const oscillator = tone.createOscillator();
        const gain = tone.createGain();
        const output = tone.createMediaStreamDestination();
        oscillator.frequency.value = 440;
        gain.gain.value = 0.02;
        oscillator.connect(gain).connect(output);
        oscillator.start();
        return output.stream;
      },
    });
  });
  await page.setViewportSize({ width: 375, height: 667 });
  await page.goto(`/${fragment(invite())}`);
  const join = page.getByRole("button", { name: "Join with microphone", exact: true });
  const meter = page.locator("#mic-meter");
  await expect(join).toBeInViewport({ ratio: 1 });
  await expect(join).toBeDisabled();
  await page.getByRole("button", { name: "Check microphone" }).click();
  await expect(meter).toHaveAttribute("aria-valuenow", /[1-9]/);
  await expect(page.getByText("Mic detected. Join when you’re ready.")).toBeVisible();
  await expect(page.getByText("Speak or play a note to check your level.")).toBeHidden();
  await expect(join).toBeDisabled();
  await page.getByRole("checkbox").check();
  await page.evaluate(() => scrollTo(0, 0));
  await expect(join).toBeEnabled();
  await expect(join).toBeInViewport({ ratio: 1 });
  await expect(page.getByRole("checkbox")).toBeInViewport({ ratio: 1 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("NotFoundError shows no-device guidance and Try again", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.addInitScript(() => {
    const prototype = Object.getPrototypeOf(navigator.mediaDevices);
    prototype.enumerateDevices = async () => [{ kind: "audioinput", deviceId: "fake", label: "Fake" }];
    prototype.getUserMedia = async () => {
      throw new DOMException("Requested device not found", "NotFoundError");
    };
  });
  await page.goto(`/${fragment(invite())}`);
  await page.getByRole("button", { name: "Check microphone" }).click();
  await expect(page.getByText("No microphone found - plug one in and press Try again")).toBeVisible();
  const retry = page.getByRole("button", { name: "Try again", exact: true });
  await expect(retry).toBeVisible();
  await retry.click();
  await expect(page.getByText("No microphone found - plug one in and press Try again")).toBeVisible();
});
