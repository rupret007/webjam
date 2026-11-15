import { test, expect } from "@playwright/test";

const invite = () => {
  const capability = Buffer.alloc(32, 7).toString("base64url");
  return `#k=${capability}&s=${"1".repeat(64)}&g=1&e=${Math.floor(Date.now() / 1000) + 600}`;
};

async function cameraPage(page, baseURL, relayStatus = 200, failure = "") {
  await page.route("**/*", route => {
    const url = new URL(route.request().url());
    if (url.origin === baseURL) return route.continue();
    if (url.pathname === "/turn-credentials") {
      expect(route.request().postDataJSON().client).toBe("web-video");
      return route.fulfill({ status: relayStatus, json: {
        client: "web-video", quota_bytes: 64 * 1024 * 1024, ttl: 600,
        iceServers: [{urls:"turn:turn.cloudflare.com:3478?transport=udp", username:"fixture", credential:"fixture"}],
      }});
    }
    if (url.pathname.startsWith("/video-mailbox/")) return route.fulfill({status:204});
    throw Error("Unexpected request from camera test");
  });
  await page.goto(`/?cameras=1${invite()}`);
  await page.evaluate((failure) => {
    window.cameraCalls = 0;
    window.peers = 0;
    window.previewPlayed = false;
    const getUserMedia = async ({audio}) => {
      if (audio) throw Error("Camera must not open the microphone");
      window.cameraCalls++;
      if (failure) throw new DOMException("Fixture camera error", failure);
      // Keep the layout fixture independent of camera permissions and canvas capture.
      const track = Object.assign(new EventTarget(), {
        readyState: "live", enabled: true, getSettings: () => ({}), stop() {},
      });
      return { getVideoTracks: () => [track], getTracks: () => [track] };
    };
    Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia } });
    window.RTCPeerConnection = class { constructor() { window.peers++; } };
  }, failure);
  await page.evaluate(() => {
    const preview = document.querySelector("[data-self]");
    Object.defineProperties(preview, {
      srcObject: { configurable: true, writable: true, value: null },
      play: { configurable: true, value() {
        window.previewPlayed = true;
        return Promise.resolve();
      } },
    });
  });
}

for (const size of [{width:390,height:844},{width:1440,height:900}]) {
  test(`camera preview and Leave fit at ${size.width} × ${size.height}`, async ({ page, baseURL }, info) => {
    await page.setViewportSize(size);
    await cameraPage(page, baseURL);
    const camera = page.getByRole("button", { name:"Show my camera" });
    const leave = page.getByRole("button", { name:"Leave", exact:true });
    await expect(camera).toBeEnabled();
    expect(await page.evaluate(() => [window.cameraCalls, window.peers])).toEqual([0,0]);
    await expect(leave).toBeInViewport({ratio:1});
    await camera.click();
    await expect.poll(() => page.evaluate(() => [window.cameraCalls, window.previewPlayed])).toEqual([1,true]);
    await expect(page.locator("[data-video-status]")).toContainText("Camera preview is on");
    await expect(page.locator("[data-self]")).toBeVisible();
    expect(await page.evaluate(() => [window.cameraCalls, window.peers, window.previewPlayed])).toEqual([1,0,true]);
    const stage = await page.locator(".video-stage").boundingBox();
    const preview = await page.locator("[data-self]").boundingBox();
    expect(stage.width * stage.height).toBeGreaterThan(preview.width * preview.height);
    expect(stage.y + stage.height).toBeLessThanOrEqual(preview.y);
    for (const button of [page.getByRole("button", { name:"Hide camera" }), leave]) {
      await expect(button).toBeInViewport({ratio:1});
      expect((await button.boundingBox()).height).toBeGreaterThanOrEqual(44);
    }
    expect(await page.locator("[data-self]").evaluate(el => getComputedStyle(el).transform)).toContain("-1");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath(`camera-preview-${size.width}.png`) });
    await leave.click();
    await expect(page.locator("[data-video-status]")).toHaveText("You left the camera.");
    await expect(page.locator("[data-self]")).toBeHidden();
  });
}

test("keyboard focus stays on the camera through a delayed permission denial", async ({ page, baseURL }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await cameraPage(page, baseURL, 200, "NotAllowedError");
  await page.evaluate(() => {
    const devices = navigator.mediaDevices;
    const capture = devices.getUserMedia;
    window.promptRequests = 0;
    devices.getUserMedia = async request => {
      window.promptRequests++;
      await new Promise(resolve => { window.finishCameraPrompt = resolve; });
      return capture(request);
    };
  });
  const camera = page.getByRole("button", { name: "Show my camera", exact: true });
  await expect(camera).toBeVisible();
  await expect(camera).toBeEnabled();
  await camera.focus();
  await expect(camera).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(camera).toHaveAttribute("aria-disabled", "true");
  await expect(camera).toBeFocused();
  await expect(page.locator("[data-video-status]")).toHaveText(
    "Waiting for camera access. If your browser asks, choose Allow.",
  );
  await expect(page.getByRole("button", { name: "Leave", exact: true })).toBeInViewport({ ratio: 1 });
  await page.keyboard.press("Enter");
  expect(await page.evaluate(() => window.promptRequests)).toBe(1);
  await page.evaluate(() => window.finishCameraPrompt());
  const retry = page.getByRole("button", { name: "Try again", exact: true });
  await expect(retry).toBeEnabled();
  await expect(retry).toBeFocused();
  expect(await page.evaluate(() => [window.cameraCalls, window.peers])).toEqual([1, 0]);
});

test("missing video relay leaves the camera off", async ({ page, baseURL }) => {
  await cameraPage(page, baseURL, 503);
  await expect(page.getByRole("button", { name:"Retry", exact:true })).toBeInViewport({ratio:1});
  await page.getByRole("button", { name:"Retry", exact:true }).click();
  expect(await page.evaluate(() => [window.cameraCalls, window.peers])).toEqual([0,0]);
});

for (const [error, message] of [["NotAllowedError", "Camera access was blocked"], ["NotFoundError", "No camera found"]]) {
  test(`camera reports ${error} without writing signaling`, async ({ page, baseURL }) => {
  await cameraPage(page, baseURL, 200, error);
  await page.getByRole("button", { name:"Show my camera" }).click();
  await expect(page.locator("[data-video-status]")).toContainText(message);
  await expect(page.getByRole("button", { name:"Leave", exact:true })).toBeInViewport({ratio:1});
  expect(await page.evaluate(() => [window.cameraCalls, window.peers])).toEqual([1,0]);
});
}
