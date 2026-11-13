import { test, expect } from "@playwright/test";
import { toBase64Url, fromHex } from "../../crypto.js";
const guest = () => {
  const value = {
    capability: fromHex("11".repeat(32)),
    session: "22".repeat(32),
    generation: 1,
    expiresAt: Math.floor(Date.now() / 1000) + 300,
  };
  return `/#k=${toBase64Url(value.capability)}&s=${value.session}&g=${value.generation}&e=${value.expiresAt}`;
};

async function installShareSpy(page) {
  await page.addInitScript(() => {
    window.shareCalls = [];
    Object.defineProperty(navigator, "share", {
      configurable: true,
      value: (data) => {
        // This Chromium reports userActivation during automation. The click
        // turn is the transient activation, so a timer or evaluate does not count.
        const active = !!window.event;
        window.shareCalls.push({
          active,
          title: data && data.title,
          text: data && data.text,
          url: data && data.url,
        });
        if (!active) return Promise.reject(new DOMException("denied", "NotAllowedError"));
        return Promise.resolve();
      },
    });
  });
}

function shareSummary(calls) {
  return calls.map((call) => ({
    active: call.active,
    title: call.title,
    text: call.text,
    example: typeof call.url === "string" && call.url.startsWith("https://example.com/"),
    fragment: typeof call.url === "string" && call.url.includes("#") && call.url.split("#")[1].length > 0,
  }));
}

test("opening an invite starts listen-only join, and the host dialog leads with Share", async ({ page, browserName }, testInfo) => {
  test.skip(browserName !== "chromium", "screenshots are Chromium only");
  await page.route("**/session.js", (route) => route.fulfill({
    contentType: "text/javascript",
    body: `export class WebJoinSession {
      constructor(options) { this.options = options; }
      join() {
        this.options.onStatus("Checking the host invitation…");
        return new Promise(() => {});
      }
      close() {}
    }`,
  }));
  for (const [width, height] of [[390, 844], [1440, 900]]) {
    await page.setViewportSize({ width, height });
    await page.goto(guest());
    await expect(page.locator("#status")).toHaveText("Checking the host invitation…");
    await expect(page.locator("#heading")).toHaveText("Joining the room…");
    await expect(page.locator("#join")).toBeHidden();
    const leave = page.getByRole("button", { name: "Leave session", exact: true });
    await expect(leave).toBeVisible();
    await expect(leave).toHaveClass(/secondary/);
    await expect(leave).toHaveClass(/quiet/);
    await expect(leave).not.toBeFocused();
    await expect(page.locator("#prejoin")).toBeHidden();
    await page.screenshot({ path: testInfo.outputPath(`guest-join-${width}x${height}.png`) });
  }
  await installShareSpy(page);
  for (const [width, height] of [[390, 844], [1440, 900]]) {
    await page.setViewportSize({ width, height });
    await page.goto("/host.html");
    await expect(page.getByRole("button", { name: "Share invite" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Copy invite" })).toBeEnabled();
    await expect(page.getByRole("link", { name: "Email invite" })).toBeHidden();
    expect(await page.evaluate(() => window.shareCalls.length)).toBe(0);
    await page.screenshot({ path: testInfo.outputPath(`host-invite-${width}x${height}.png`) });
  }
});

test("Share invite runs only from a trusted click", async ({ page }) => {
  await installShareSpy(page);
  await page.goto("/host.html");
  await expect(page.getByRole("button", { name: "Share invite" })).toBeEnabled();
  const evaluated = await page.evaluate(async () => {
    try {
      await navigator.share({ title: "Join me on WebJam", url: "https://example.com/join/#invite" });
      return "resolved";
    } catch (error) {
      return error.name;
    }
  });
  expect(evaluated).toBe("NotAllowedError");
  expect(shareSummary(await page.evaluate(() => window.shareCalls))).toEqual([{
    active: false,
    title: "Join me on WebJam",
    text: undefined,
    example: true,
    fragment: true,
  }]);
  await page.getByRole("button", { name: "Share invite" }).click();
  const trusted = shareSummary(await page.evaluate(() => window.shareCalls.filter((call) => call.active)));
  expect(trusted).toEqual([{
    active: true,
    title: "Join me on WebJam",
    text: "Tap to listen, no app, no microphone.",
    example: true,
    fragment: true,
  }]);
});

test("a no-activation share rejects and Copy stays usable", async ({ page }) => {
  await installShareSpy(page);
  await page.goto("/host.html");
  await page.waitForFunction(() => typeof window.webjamShareInvite === "function");
  expect(await page.evaluate(() => window.shareCalls.length)).toBe(0);
  const rejected = await page.evaluate(() => new Promise((resolve) => {
    setTimeout(() => {
      try {
        const pending = window.webjamShareInvite();
        Promise.resolve(pending).then(() => resolve("resolved"), (error) => resolve(error.name));
      } catch (error) {
        resolve(error.name);
      }
    }, 0);
  }));
  expect(rejected).toBe("NotAllowedError");
  expect(await page.evaluate(() => window.shareCalls.length)).toBe(0);
  const copy = page.getByRole("button", { name: "Copy invite" });
  await expect(copy).toBeEnabled();
  await copy.click();
  await expect(copy).toBeEnabled();
});

test("Copy invite includes the listen sentence and the example link", async ({ page }) => {
  await page.addInitScript(() => {
    window.copied = [];
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: async (value) => { window.copied.push(String(value)); } },
    });
  });
  await page.goto("/host.html");
  await page.getByRole("button", { name: "Copy invite" }).click();
  expect(await page.evaluate(() => window.copied)).toEqual([
    "Tap to listen, no app, no microphone.\nhttps://example.com/join/#invite",
  ]);
});

test("Email invite leads when navigator.share is absent", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "share", { configurable: true, value: undefined });
    window.openedMail = [];
    window.open = (url) => {
      window.openedMail.push(String(url));
      return null;
    };
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/host.html");
  await expect(page.getByRole("button", { name: "Share invite" })).toBeHidden();
  const email = page.getByRole("link", { name: "Email invite" });
  await expect(email).toBeVisible();
  await expect(email).not.toHaveClass(/secondary/);
  const copy = page.getByRole("button", { name: "Copy invite" });
  await expect(copy).toBeEnabled();
  await expect(copy).toHaveClass(/secondary/);
  await email.click();
  const mailed = await page.evaluate(() => {
    const href = window.openedMail[0] || "";
    const parsed = new URL(href);
    const body = parsed.searchParams.get("body") || "";
    return {
      protocol: parsed.protocol,
      subject: parsed.searchParams.get("subject"),
      sentence: body.split("\n")[0],
      example: (body.split("\n")[1] || "").startsWith("https://example.com/"),
      fragment: body.includes("#") && body.split("#")[1].length > 0,
      deliver: body.includes("deliver"),
    };
  });
  expect(mailed).toEqual({
    protocol: "mailto:",
    subject: "Join me on WebJam",
    sentence: "Tap to listen, no app, no microphone.",
    example: true,
    fragment: true,
    deliver: false,
  });
  await expect(copy).toBeEnabled();
});
