import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./test/e2e",
  timeout: 300_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "chromium", testIgnore: /peer-cleanup\.spec\.js/, use: { browserName: "chromium" } },
    { name: "webkit", testIgnore: /peer-cleanup\.spec\.js/, use: { browserName: "webkit" } },
    { name: "chromium-peer-cleanup", testMatch: /peer-cleanup\.spec\.js/, use: { browserName: "chromium" } },
  ],
  webServer: {
    command: "node test/e2e/server.js",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: false,
    timeout: 15_000,
  },
});
