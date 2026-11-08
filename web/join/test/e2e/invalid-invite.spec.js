import { test, expect } from "@playwright/test";

for (const width of [375, 390]) {
  for (const kind of ["missing", "malformed", "expired"]) {
    test(`${kind} invite gives an actionable fresh-link recovery at ${width}px without capture`, async ({ page, baseURL }) => {
      await page.setViewportSize({ width, height: 844 });
      await page.addInitScript(() => {
        window.captureCalls = 0;
        const prototype = Object.getPrototypeOf(navigator.mediaDevices);
        for (const name of ["getUserMedia", "getDisplayMedia"]) {
          Object.defineProperty(prototype, name, { configurable: true, value: async () => {
            window.captureCalls++;
            throw new Error("No capture is allowed for an unusable invite");
          } });
        }
      });
      const external = [];
      await page.route("**/*", route => {
        if (new URL(route.request().url()).origin === baseURL) return route.continue();
        external.push(route.request().url());
        return route.abort();
      });
      const expires = Math.floor(Date.now() / 1000) - 1;
      const value = kind === "missing" ? "" : kind === "malformed" ? "#k=truncated" :
        `#k=${Buffer.alloc(32, 17).toString("base64url")}&s=${"22".repeat(32)}&g=1&e=${expires}`;
      await page.goto(`/${value}`);
      await expect(page.locator("#status")).toHaveAttribute("data-failure", "true");
      await expect(page.locator("#error-hint")).toHaveText("Ask your host for a new browser invite and open the full link.");
      await expect(page.locator("#error-hint")).not.toContainText(/retry/i);
      for (const id of ["prejoin", "join", "retry", "leave"]) await expect(page.locator(`#${id}`)).toBeHidden();
      expect(await page.evaluate(() => window.captureCalls)).toBe(0);
      expect(external).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
  }
}
