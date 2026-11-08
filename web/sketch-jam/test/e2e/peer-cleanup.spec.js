import { test, expect } from "@playwright/test";
import { installFakeAudio } from "./fake-audio.js";

test.beforeEach(async ({ request }) => {
  await request.post("/__test/reset", { data: {} });
});

test("leave clears connected peers from the list", async ({ browser }) => {
  const context = await browser.newContext({
    baseURL: "http://127.0.0.1:4173",
    extraHTTPHeaders: { "x-sketch-test-client": "peer-cleanup" },
  });
  await installFakeAudio(context, { nativeMockCapture: false });
  const page = await context.newPage();
  try {
    await page.goto("/");
    await page.locator("#name").fill("Host");
    await page.locator("#headphones").check();
    await page.locator("#join").click();
    await expect(page.locator("#leave")).toBeEnabled({ timeout: 30_000 });
    await page.evaluate(() => {
      const entry = document.createElement("li");
      entry.id = "peer-0";
      entry.dataset.state = "connected";
      entry.dataset.peer = "0";
      document.getElementById("peers").append(entry);
    });
    await expect(page.locator("#peers [data-state='connected']")).toHaveCount(1);
    await page.locator("#leave").click();
    await expect(page.locator("#peers")).toBeEmpty();
  } finally {
    await context.close();
  }
});
