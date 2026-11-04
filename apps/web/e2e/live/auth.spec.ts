import { expect, test } from "@playwright/test";

import { LIVE_SKIP_REASON } from "../support/live-config";

/**
 * Live layer — real sign-in against a deployed API.
 *
 * This is the only layer that proves the login form talks to the actual Auth
 * Service contract (`POST /auth/login` → `{access_token, refresh_token}` → `/me`).
 * The smoke layer mocks that contract, so a changed wire shape would pass there.
 *
 * Credentials are supplied by the operator; no default account is invented here
 * (see `e2e/README.md`).
 */
test.describe("live sign-in @live", () => {
  // The describe body must still be evaluated for the skip to be reported per test.
  const configured = Boolean(process.env.E2E_API_URL && process.env.E2E_USERNAME && process.env.E2E_PASSWORD);

  test.beforeEach(async () => {
    test.skip(!configured, LIVE_SKIP_REASON);
  });

  test("a real employee can sign in and reach the workspace", async ({ page }) => {
    // The preview server cannot proxy an arbitrary remote API, so the browser calls
    // the real host directly. CORS on that host must allow the preview origin; the
    // operator-facing README documents this. Requests are proxied through
    // `page.route()` continuation to keep the origin/route assertions explicit.
    const apiBase = (process.env.E2E_API_URL ?? "").replace(/\/$/, "");
    await page.route(`**/api/v1/**`, async (route) => {
      const url = new URL(route.request().url());
      await route.continue({ url: `${apiBase}${url.pathname.replace(/^\/api\/v1/, "")}${url.search}` });
    });

    await page.goto("/login");
    await page.getByLabel("Email or username").fill(process.env.E2E_USERNAME ?? "");
    await page.getByLabel("Password").fill(process.env.E2E_PASSWORD ?? "");
    await page.getByRole("button", { name: "Sign in" }).click();

    // A successful real login must reach the authenticated shell, not stay on /login
    // with a red alert. Failing here with the alert visible is the expected shape of
    // a credentials/permission problem, so assert on the URL and the frame.
    await expect(page).not.toHaveURL(/\/login/, { timeout: 20_000 });
    await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Application navigation" })).toBeVisible();
  });

  test("the real /me response satisfies the client's identity contract", async ({ page }) => {
    const apiBase = (process.env.E2E_API_URL ?? "").replace(/\/$/, "");
    await page.route(`**/api/v1/**`, async (route) => {
      const url = new URL(route.request().url());
      await route.continue({ url: `${apiBase}${url.pathname.replace(/^\/api\/v1/, "")}${url.search}` });
    });

    await page.goto("/login");
    await page.getByLabel("Email or username").fill(process.env.E2E_USERNAME ?? "");
    await page.getByLabel("Password").fill(process.env.E2E_PASSWORD ?? "");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).not.toHaveURL(/\/login/, { timeout: 20_000 });

    // `normalizeCurrentUser()` throws unless `active`, `roles`, `contours` and
    // `id`/subject are present; a contract drift turns into a visible identity
    // failure on the projects screen rather than a silent anonymous shell.
    await page.goto("/cabinets");
    await expect(page.getByRole("heading", { name: "Cabinets", level: 1 })).toBeVisible();
    // The account label comes from `/me`; the placeholder means identity was lost.
    await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
    await expect(page.locator("header .employee-app-account span").first()).not.toHaveText("Employee");
  });
});
