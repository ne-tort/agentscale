import { expect, test } from "@playwright/test";

import { clearSession, mockApi, seedSession } from "../support/api-mock";

/**
 * Smoke layer 2 — route guarding.
 *
 * `RequireAuth` decides between a loading state, the shell and a redirect to
 * `/login?next=…`. That gate only exists at runtime: vitest renders providers with
 * hand-made context, so a broken `Navigate`/`Outlet` wiring would pass unit tests
 * and strand real users on a blank frame.
 */
test.describe("protected routes", () => {
  test.beforeEach(async ({ page }) => {
    await clearSession(page);
  });

  test("an anonymous visit to a protected route redirects to /login", async ({ page }) => {
    await mockApi(page);
    await page.goto("/projects");

    await expect(page).toHaveURL(/\/login\?next=%2Fprojects$/);
    await expect(page.getByRole("heading", { name: "Sign in to agentscale" })).toBeVisible();
    // The protected shell must not have painted behind the redirect.
    await expect(page.getByRole("heading", { name: "Проекты", level: 1 })).toHaveCount(0);
  });

  test("the redirect keeps the requested path in ?next= and honours it after sign-in", async ({ page }) => {
    await mockApi(page);
    await page.goto("/cabinets");

    await expect(page).toHaveURL(/\/login\?next=%2Fcabinets$/);

    await page.getByLabel("Email or username").fill("e2e@example.test");
    await page.getByLabel("Password").fill("secret");
    await page.getByRole("button", { name: "Sign in" }).click();

    await expect(page).toHaveURL(/\/cabinets$/);
    await expect(page.getByRole("heading", { name: "Cabinets", level: 1 })).toBeVisible();
  });

  test("a nested protected route redirects with its full path", async ({ page }) => {
    await mockApi(page);
    await page.goto("/projects/project-e2e/files");

    await expect(page).toHaveURL(/\/login\?next=%2Fprojects%2Fproject-e2e%2Ffiles$/);
  });

  test("a persisted session restores without a redirect", async ({ page }) => {
    await mockApi(page);
    await seedSession(page);
    await page.goto("/cabinets");

    // `AuthLifecycle.recover()` validates the stored tokens through `/me`.
    await expect(page).toHaveURL(/\/cabinets$/);
    await expect(page.getByRole("heading", { name: "Cabinets", level: 1 })).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  });

  test("a session the API rejects is dropped and the user is sent back to /login", async ({ page }) => {
    await mockApi(page, {
      overrides: {
        "/me": async (route) => {
          await route.fulfill({
            status: 401,
            contentType: "application/json",
            body: JSON.stringify({ code: "UNAUTHORIZED", title: "Unauthorized", status: 401 }),
          });
        },
      },
    });
    await seedSession(page);
    await page.goto("/projects");

    await expect(page).toHaveURL(/\/login\?next=%2Fprojects$/);
    // The rejected credentials must not survive the reload.
    await expect.poll(async () => page.evaluate(() => window.localStorage.getItem("agentscale.web.session"))).toBeNull();
  });

  test("signing out returns to /login and clears the stored session", async ({ page }) => {
    await mockApi(page);
    await seedSession(page);
    await page.goto("/cabinets");
    await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();

    await page.getByRole("button", { name: "Sign out" }).click();

    // Sign-out drops the session and the guard re-applies on the current route.
    // `RequireAuth` keeps the signed-out location in `?next=`, so a user who signs
    // back in lands on the page they left instead of the projects list.
    await expect(page).toHaveURL(/\/login(\?next=%2Fcabinets)?$/);
    await expect(page.getByRole("heading", { name: "Sign in to agentscale" })).toBeVisible();
    await expect.poll(async () => page.evaluate(() => window.localStorage.getItem("agentscale.web.session"))).toBeNull();
  });
});
