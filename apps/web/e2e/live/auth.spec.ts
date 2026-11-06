import { expect, test } from "@playwright/test";

import { E2E_LIVE_CREDENTIALS, LIVE_SKIP_REASON } from "../support/live-config";
import { installApiProxy, signIn } from "../support/live";

/**
 * Live layer — real sign-in and identity.
 *
 * Proves the parts the mocked smoke layer cannot: the real `POST /auth/login`
 * wire shape, the `/me` projection `normalizeCurrentUser()` accepts, and the
 * session surviving a full navigation.
 *
 * Skipped unless `E2E_API_URL` + `E2E_USERNAME` + `E2E_PASSWORD` are exported.
 */
test.describe("live sign-in @live", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!E2E_LIVE_CREDENTIALS, LIVE_SKIP_REASON);
    await installApiProxy(page, E2E_LIVE_CREDENTIALS!.apiUrl);
  });

  test("a real employee signs in and reaches the authenticated frame", async ({ page }) => {
    await signIn(page, E2E_LIVE_CREDENTIALS!);

    await expect(page.getByRole("navigation", { name: "Application navigation" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  });

  test("the session survives a reload through the real refresh/me path", async ({ page }) => {
    await signIn(page, E2E_LIVE_CREDENTIALS!);

    await page.reload();

    // A reload re-enters `AuthLifecycle.recover()`, which re-validates `/me`.
    await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
    await expect(page).not.toHaveURL(/\/login/);
    // The identity label must come from the API, not from the "Employee" fallback.
    await expect(page.locator("header .employee-app-account span").first()).not.toHaveText("Employee");
  });

  test("signing out revokes the session and returns to /login", async ({ page }) => {
    await signIn(page, E2E_LIVE_CREDENTIALS!);

    await page.getByRole("button", { name: "Sign out" }).click();

    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByRole("heading", { name: "Sign in to agentscale" })).toBeVisible();

    // Navigating back must not resurrect the revoked session.
    await page.goto("/projects");
    await expect(page).toHaveURL(/\/login/);
  });
});
