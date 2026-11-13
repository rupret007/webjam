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

test("a browser without Web Audio offers a way back to Hang Out", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => { window.AudioContext = undefined; });
  await page.goto(guest());
  const status = page.getByRole("status").and(page.locator("#status"));
  await expect(status).toHaveText("This browser can’t play WebJam audio.");
  await expect(status).toHaveAccessibleDescription(/return to Hang Out to keep chatting/i);
  for (const id of ["listen", "join", "prejoin", "retry"]) {
    await expect(page.locator(`#${id}`)).toBeHidden();
  }
});

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
    example: typeof call.url === "string" && call.url.startsWith("https://example.com/"),
    fragment: typeof call.url === "string" && call.url.includes("#") && call.url.split("#")[1].length > 0,
  }));
}

test("guest Join and the host invite dialog at phone and desktop sizes", async ({ page }, testInfo) => {
  for (const [width, height] of [[390, 844], [1440, 900]]) {
    await page.setViewportSize({ width, height });
    await page.goto(guest());
    const join = page.getByRole("button", { name: "Join to listen", exact: true });
    await expect(join).toBeEnabled();
    await expect(join).toBeInViewport();
    await expect(join).toHaveAccessibleDescription("Your microphone stays off.");
    await expect(page.getByRole("button", { name: "Join with microphone" })).toBeDisabled();
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

test("a guest retries listening and leaves without requesting a microphone", async ({ page }) => {
  await page.addInitScript(() => {
    window.micRequests = 0;
    navigator.mediaDevices.getUserMedia = () => {
      window.micRequests++;
      return Promise.reject(new Error("Unexpected capture"));
    };
  });
  await page.route("**/session.js", (route) => route.fulfill({
    contentType: "text/javascript",
    body: `let attempts = 0;
    export class WebJoinSession {
      constructor(options) {
        if (options.stream) throw new Error("Unexpected microphone stream");
      }
      async join() {
        if (++attempts === 1) throw new DOMException("Interrupted", "AbortError");
        this.authorized = true;
      }
      close() {}
    }`,
  }));
  await page.goto(guest());
  const listen = page.getByRole("button", { name: "Join to listen" });
  await listen.focus();
  await listen.press("Enter");
  await expect(page.getByText("Check your connection, then select Retry to listen again.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Check microphone" })).toBeHidden();
  const retry = page.getByRole("button", { name: "Retry", exact: true });
  await expect(retry).toBeFocused();
  await expect(retry).toHaveAccessibleDescription("Check your connection, then select Retry to listen again.");
  await retry.press("Enter");
  await expect(page.getByRole("status").filter({ hasText: "You’re listening with your microphone off." })).toBeVisible();
  const leave = page.getByRole("button", { name: "Leave session" });
  await expect(leave).toBeFocused();
  await leave.press("Enter");
  await expect(page.locator("#status")).toHaveAccessibleDescription(/return to your Jam room to accept it/i);
  expect(await page.evaluate(() => window.micRequests)).toBe(0);
});

test("choosing listening immediately releases a microphone check already in progress", async ({ page }) => {
  await page.addInitScript(() => {
    let resumes = 0;
    const track = Object.assign(new EventTarget(), {
      readyState: "live", getSettings: () => ({}), stop: () => { window.micStopped = true; },
    });
    navigator.mediaDevices.getUserMedia = () => new Promise(resolve => {
      window.completeMicRequest = () => resolve({ getAudioTracks: () => [track], getTracks: () => [track] });
    });
    window.AudioContext = class {
      resume() {
        if (++resumes === 1) return Promise.resolve();
        return new Promise(resolve => { window.completeListeningResume = resolve; });
      }
      close() { return Promise.resolve(); }
    };
  });
  await page.route("**/session.js", route => route.fulfill({
    contentType: "text/javascript",
    body: `export class WebJoinSession {
      constructor({stream}) { if (stream) throw new Error("Unexpected microphone stream"); }
      async join() { this.authorized = true; }
      close() {}
    }`,
  }));
  await page.goto(guest());
  await page.getByRole("button", { name: "Check microphone" }).click();
  await page.waitForFunction(() => typeof window.completeMicRequest === "function");
  await page.getByRole("button", { name: "Join to listen" }).press("Enter");
  await page.waitForFunction(() => typeof window.completeListeningResume === "function");
  await page.evaluate(() => window.completeMicRequest());
  await page.waitForFunction(() => window.micStopped === true);
  await page.evaluate(() => window.completeListeningResume());
  await expect(page.locator("#status")).toContainText("listening with your microphone off");
  await page.getByRole("button", { name: "Leave session" }).press("Enter");
  await expect(page.locator("#status")).toBeFocused();
});

test("cached join markup keeps the microphone path usable", async ({ page }) => {
  await page.addInitScript(() => {
    const observer = new MutationObserver(() => {
      const controls = document.getElementById("listen-actions");
      if (!controls) return;
      controls.remove();
      observer.disconnect();
    });
    observer.observe(document, { childList: true, subtree: true });
  });
  await page.goto(guest());
  await expect(page.locator("#status")).toHaveText("Your host invited you to join.");
  await expect(page.locator("#listen")).toHaveCount(0);
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Skip check" }).press("Enter");
  const join = page.getByRole("button", { name: "Join with microphone" });
  await expect(join).toBeEnabled();
  await expect(join).toBeFocused();
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
    example: true,
    fragment: true,
  }]);
  await page.getByRole("button", { name: "Share invite" }).click();
  const trusted = shareSummary(await page.evaluate(() => window.shareCalls.filter((call) => call.active)));
  expect(trusted).toEqual([{
    active: true,
    title: "Join me on WebJam",
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
      example: body.startsWith("https://example.com/"),
      fragment: body.includes("#") && body.split("#")[1].length > 0,
      deliver: body.includes("deliver"),
    };
  });
  expect(mailed).toEqual({
    protocol: "mailto:",
    subject: "Join me on WebJam",
    example: true,
    fragment: true,
    deliver: false,
  });
  await expect(copy).toBeEnabled();
});
