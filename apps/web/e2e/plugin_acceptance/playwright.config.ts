import { defineConfig } from "@playwright/test";

import { PLUGIN_ACCEPTANCE } from "./support/environment";

export default defineConfig({
  testDir: ".",
  testMatch: "**/*.acceptance.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: true,
  timeout: PLUGIN_ACCEPTANCE.expiryMaxWaitMs + 90_000,
  expect: { timeout: 15_000 },
  outputDir: "test-results",
  reporter: [["list"], ["./no-skip-reporter.ts"]],
  use: {
    baseURL: PLUGIN_ACCEPTANCE.webOrigin,
    browserName: "chromium",
    headless: true,
    viewport: { width: 1440, height: 1000 },
    actionTimeout: 15_000,
    navigationTimeout: PLUGIN_ACCEPTANCE.httpTimeoutMs,
    ignoreHTTPSErrors: false,
    storageState: { cookies: [], origins: [] },
    // Avoid persisting Authorization headers, session bodies or tenant data.
    trace: "off",
    screenshot: "off",
    video: "off",
  },
  projects: [{ name: "plugin-acceptance-chromium", use: { browserName: "chromium" } }],
});
