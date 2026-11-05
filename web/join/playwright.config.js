import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
export default defineConfig({
  testDir: "./test/e2e", timeout: 30000, expect: { timeout: 10000 }, workers: 1, retries: 0,
  use: { baseURL: "http://127.0.0.1:4174" },
  projects: [
    { name: "chromium", testIgnore: /prejoin\.spec\.js/, use: { browserName: "chromium", launchOptions: { args: ["--disable-features=WebRtcHideLocalIpsWithMdns"] } } },
    {
      name: "chromium-fake-mic",
      testMatch: /prejoin\.spec\.js/,
      use: {
        browserName: "chromium",
        launchOptions: {
          args: [
            "--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream",
            "--disable-features=WebRtcHideLocalIpsWithMdns",
          ],
        },
      },
    },
    { name: "webkit", testIgnore: /prejoin\.spec\.js/, use: { browserName: "webkit", permissions: ["microphone"] } },
  ],
  webServer: [
    { command: "node test/e2e/server.js", url: "http://127.0.0.1:4174", reuseExistingServer: false, timeout: 15000 },
    { command: "WEBJAM_BROWSER_LAB=1 go test ./internal/webjoin -run '^$' -count=1", cwd: fileURLToPath(new URL("../../transport", import.meta.url)), url: "http://127.0.0.1:4175/status", reuseExistingServer: false, timeout: 600000 },
  ],
});
