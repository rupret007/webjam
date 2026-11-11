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
  const guidance = page.locator("#join-guidance");
  await expect(guidance).toHaveText("Check your microphone first.");
  await page.getByRole("button", { name: "Check microphone" }).press("Enter");
  await expect(meter).toHaveAttribute("aria-valuenow", /[1-9]/);
  await expect(page.getByText("Microphone detected.", { exact: true })).toBeVisible();
  await expect(page.getByText("Speak or play a note to check your level.")).toBeHidden();
  await expect(join).toBeDisabled();
  await expect(guidance).toHaveText("Confirm your headphones to enable Join.");
  const headphones = page.getByRole("checkbox");
  await expect(headphones).toBeFocused();
  await page.keyboard.press("Space");
  await expect(guidance).toHaveText("Ready. Join to share your audio with the session.");
  await expect(join).toHaveAttribute("aria-describedby", "join-guidance");
  await page.getByRole("checkbox").uncheck();
  await expect(join).toBeDisabled();
  await expect(guidance).toHaveText("Confirm your headphones to enable Join.");
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
  await page.getByRole("button", { name: "Check microphone" }).press("Enter");
  await expect(page.getByText("No microphone found - plug one in and press Try again")).toBeVisible();
  const retry = page.getByRole("button", { name: "Try again", exact: true });
  await expect(retry).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByText("No microphone found - plug one in and press Try again")).toBeVisible();
  await expect(retry).toBeFocused();
});

test("a guest can replace a silent browser default with a detected headset", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 667 });
  await page.addInitScript(() => {
    window.micTracks = [];
    window.micChoices = [];
    const media = Object.getPrototypeOf(navigator.mediaDevices);
    media.enumerateDevices = async () => [
      { kind: "audioinput", deviceId: "array", label: "Built-in mic" },
      { kind: "audioinput", deviceId: "headset", label: "USB headset" },
    ];
    media.getUserMedia = async ({ audio }) => {
      const headset = audio.deviceId?.exact === "headset";
      window.micChoices.push(audio.deviceId?.exact ?? "");
      const context = new AudioContext();
      const output = context.createMediaStreamDestination();
      const track = output.stream.getAudioTracks()[0];
      Object.defineProperty(track, "label", { value: headset ? "USB headset" : "Built-in mic" });
      const stop = track.stop.bind(track);
      track.stop = () => { stop(); context.close().catch(() => {}); };
      window.micTracks.push(track);
      if (headset) {
        await context.resume();
        const tone = context.createOscillator();
        const gain = context.createGain();
        gain.gain.value = 0.08;
        tone.connect(gain).connect(output);
        tone.start();
      }
      return output.stream;
    };
  });
  await page.goto(`/${fragment(invite())}`);
  const mic = page.getByLabel("Microphone", { exact: true });
  const join = page.getByRole("button", { name: "Join with microphone", exact: true });
  await expect(mic).toBeHidden();
  expect(await page.evaluate(() => micTracks.length)).toBe(0);
  await page.getByRole("button", { name: "Check microphone" }).click();
  await expect(page.getByText("Using Built-in mic", { exact: true })).toBeVisible();
  await expect(join).toBeDisabled();
  await mic.selectOption({ label: "USB headset" });
  await expect(page.getByText("Using USB headset", { exact: true })).toBeVisible();
  await expect(page.getByText("Microphone detected.", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => micChoices)).toEqual(["", "headset"]);
  expect(await page.evaluate(() => micTracks[0].readyState)).toBe("ended");
  await page.getByRole("checkbox").check();
  await expect(join).toBeEnabled();
  await page.evaluate(() => scrollTo(0, 0));
  await expect(join).toBeInViewport({ ratio: 1 });
});

for (const width of [375, 390]) {
  test(`headphones-first readiness and explicit skip at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 667 });
    await page.goto(`/${fragment(invite())}`);
    const join = page.getByRole("button", { name: "Join with microphone", exact: true });
    const guidance = page.locator("#join-guidance");
    await page.getByRole("checkbox").check();
    await expect(join).toBeDisabled();
    await expect(guidance).toHaveText("Check your microphone first.");
    await page.getByRole("button", { name: "Skip check", exact: true }).click();
    await expect(join).toBeEnabled();
    await expect(guidance).toHaveText("Ready. Join to share your audio with the session.");
    await expect(page.locator("#mic-progress")).toHaveText(
      "Mic check skipped. Your browser may ask for access when you join.",
    );
    await expect(join).toBeInViewport({ ratio: 1 });
    await expect(guidance).toBeInViewport({ ratio: 1 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}
