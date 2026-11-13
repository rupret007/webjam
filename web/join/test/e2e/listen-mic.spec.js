import { test, expect } from "@playwright/test";
import { toBase64Url, fromHex } from "../../crypto.js";

const invitePath = () => {
  const value = {
    capability: fromHex("11".repeat(32)),
    session: "22".repeat(32),
    generation: 1,
    expiresAt: Math.floor(Date.now() / 1000) + 300,
  };
  return `/#k=${toBase64Url(value.capability)}&s=${value.session}&g=${value.generation}&e=${value.expiresAt}`;
};

async function prove(page, browserName, which, lab = null) {
  if (which === "pion") {
    await page.exposeFunction("webjamApplyAnswer", async (envelope) => {
      const applied = await page.request.post("http://127.0.0.1:4175/answer", { data: { envelope_b64url: envelope } });
      if (applied.ok()) return "";
      const detail = (await applied.text()).replace(/\s+/g, " ").slice(0, 80);
      return `answer rejected (${applied.status()} ${detail})`;
    });
    await page.exposeFunction("webjamLabStatus", async () => page.request.get("http://127.0.0.1:4175/status").then((response) => response.json()));
  }
  await page.goto("/test/e2e/loopback.html");
  await page.evaluate(({ name, which, lab }) => {
    document.querySelector("#start").addEventListener("click", () => {
      window.proof = import("/test/e2e/listen-mic-harness.js")
        .then((mod) => which === "pion" ? mod.runPionMicProof(name, lab) : mod.runListenMicProof(name))
        .then((value) => ({ ok: true, value }))
        .catch((error) => ({ ok: false, message: typeof error === "string" ? error : (error && error.message) || String(error), stack: typeof error === "string" ? error : String((error && error.stack) || error) }));
    });
  }, { name: browserName, which, lab });
  await page.locator("#start").click();
  const proof = await page.evaluate(() => window.proof);
  expect(proof.ok, proof.stack || proof.message).toBe(true);
  return proof.value;
}

function expectHeard(value, browserName) {
  expect(value.captureCallsAtJoin).toBe(0);
  expect(value.sendingTracksAtJoin).toBe(0);
  expect(value.outboundAtJoin).toEqual({ packets: 0, bytes: 0 });
  expect(value.inboundAtJoin.packets).toBe(0);
  expect(value.directionAtJoin).toBe("sendrecv");
  expect(value.audioSections).toBe(1);
  expect(value.attached).toBe(true);
  expect(value.inboundAfter.packets).toBeGreaterThan(0);
  expect(value.inboundAfter.bytes).toBeGreaterThan(0);
  expect(value.outboundAfter.packets).toBeGreaterThan(0);
  expect(value.signalingStable).toBe(true);
  expect(value.directionAfter).toMatch(/sendrecv|sendonly/);
  expect(value.errors).toEqual([]);
  expect(value.captureCallsAfter).toBe(browserName === "chromium" ? 1 : 0);
  if (browserName === "chromium") expect(value.trackLabel.toLowerCase()).toContain("fake");
}

test("listen-only join sends nothing until attachMicrophone, then the peer receives audio", async ({ page, browserName }) => {
  test.setTimeout(45000);
  const value = await prove(page, browserName, "browser");
  expectHeard(value, browserName);
  expect(value.inboundAtJoin).toEqual({ packets: 0, bytes: 0 });
  expect(value.remoteSdpUnchanged).toBe(true);
});

test("listen-only join then attachMicrophone is heard by the loopback host", async ({ page, request, browserName }) => {
  test.skip(browserName === "webkit", "This WebKit gathers only an mDNS candidate, which the loopback host rejects. The browser peer covers WebKit audio.");
  test.setTimeout(45000);
  const response = await request.get("http://127.0.0.1:4175/offer");
  expect(response.ok()).toBe(true);
  const value = await prove(page, browserName, "pion", await response.json());
  expectHeard(value, browserName);
});

test("the mic stays off in the room until outbound audio is accepted", async ({ page }) => {
  await page.addInitScript(() => {
    const native = window.AudioContext;
    Object.defineProperty(Object.getPrototypeOf(navigator.mediaDevices), "getUserMedia", {
      configurable: true,
      value: async () => {
        const context = new native();
        const output = context.createMediaStreamDestination();
        const oscillator = context.createOscillator();
        const gain = context.createGain();
        gain.gain.value = 0.08;
        oscillator.connect(gain).connect(output);
        oscillator.start();
        await context.resume();
        return output.stream;
      },
    });
  });
  await page.route("**/session.js", (route) => route.fulfill({
    contentType: "text/javascript",
    body: `export class WebJoinSession {
      constructor(options) { this.options = options; }
      async join() { this.options.onStatus("Synthetic session connected."); }
      async attachMicrophone() {
        window.attachAttempts = (window.attachAttempts || 0) + 1;
        return window.allowOutbound === true;
      }
      close() {}
    }`,
  }));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(invitePath());
  await expect(page.locator("#status")).toHaveText("Synthetic session connected.");
  await expect(page.locator("#mic-heard")).toBeHidden();
  await expect(page.locator("#join-guidance")).toHaveText("Listening only. Headphones prevent echo and feedback if you turn the microphone on.");
  await page.getByRole("button", { name: "Use microphone", exact: true }).click();
  await expect(page.locator("#mic-error")).toHaveText("The room cannot hear this microphone yet. You are still listening.");
  await expect(page.locator("#mic-heard")).toBeHidden();
  await expect(page.locator("#status")).toHaveText("Synthetic session connected.");
  expect(await page.evaluate(() => window.attachAttempts)).toBe(1);
  await page.evaluate(() => { window.allowOutbound = true; });
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(page.locator("#mic-heard")).toBeVisible();
  await expect(page.locator("#mic-heard")).toHaveText("Microphone on.");
  await expect(page.locator("#join-guidance")).toHaveText("Microphone on. Headphones prevent echo and feedback.");
  await expect(page.locator("#listen-note")).toBeHidden();
  await expect(page.getByText("You are listening.", { exact: true })).toHaveCount(0);
  await expect(page.locator("#mic-progress")).not.toHaveText(/Speak or play a note/);
  await expect(page.locator("#mic-error")).toHaveText("");
  expect(await page.evaluate(() => window.attachAttempts)).toBe(2);
});
