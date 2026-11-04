import { defineConfig, devices } from "@playwright/test";

/**
 * Browser E2E for `@agentscale/web`.
 *
 * The suite runs against a real production bundle served by `vite preview`
 * (closer to the shipped artifact than the dev server: hashed assets, SPA
 * fallback, no HMR client). There is deliberately a single Chromium project —
 * Firefox/WebKit binaries are not downloaded, so `--project=chromium` is the
 * only supported target and the default for every script.
 *
 * Layers:
 *  - `e2e/smoke/**` — mocked `page.route()` API, no backend required, always green.
 *  - `e2e/live/**`  — real API behind `E2E_API_URL`; skipped when it is unset.
 *
 * See `e2e/README.md` for the environment matrix.
 */

/** Fixed preview port so `webServer` and `baseURL` cannot drift apart. */
export const E2E_PREVIEW_PORT = Number(process.env.E2E_PREVIEW_PORT ?? 4173);
export const E2E_BASE_URL = `http://127.0.0.1:${E2E_PREVIEW_PORT}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  // A focused `test.only` must never reach a shared branch.
  forbidOnly: Boolean(process.env.CI),
  // Local runs stay deterministic: a flaky smoke test is a defect, not a retry.
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 2 : undefined,
  timeout: 30_000,
  expect: { timeout: 7_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: E2E_BASE_URL,
    // Traces are only produced for the first retry, i.e. in CI where retries exist.
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "off",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    // `vite build` only: `npm run build` runs the repo-wide `tsc --noEmit`
    // gate, which is a separate check and currently red in unrelated zones.
    // The bundle here must prove the browser surface, not re-run typechecking.
    command: `npm run build:e2e && npm run preview:e2e -- --port ${E2E_PREVIEW_PORT} --strictPort`,
    url: `${E2E_BASE_URL}/login`,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    stdout: "pipe",
    stderr: "pipe",
  },
});
