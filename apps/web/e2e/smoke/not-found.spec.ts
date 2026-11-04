import { expect, test } from "@playwright/test";

import { clearSession, mockApi, seedSession } from "../support/api-mock";

/**
 * Smoke layer 3 — unknown routes.
 *
 * Two distinct surfaces must answer a bad path:
 *  - anonymous: the auth gate wins, the URL is preserved in `?next=` and the 404
 *    body is never rendered (otherwise a 404 would leak the shell's navigation);
 *  - authenticated: `NotFoundPage` renders inside the frame with a way back.
 */
test.describe("unknown routes", () => {
  test("an anonymous unknown path redirects to /login and keeps the path", async ({ page }) => {
    await clearSession(page);
    await mockApi(page);
    await page.goto("/definitely/not/a/route");

    await expect(page).toHaveURL(/\/login\?next=%2Fdefinitely%2Fnot%2Fa%2Froute$/);
    await expect(page.getByRole("heading", { name: "Sign in to agentscale" })).toBeVisible();
  });

  test("an authenticated unknown path renders the 404 page inside the shell", async ({ page }) => {
    await seedSession(page);
    await mockApi(page);
    await page.goto("/definitely/not/a/route");

    await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
    // The detail echoes the attempted path, proving the router matched the catch-all.
    await expect(page.getByText("/definitely/not/a/route")).toBeVisible();
    await expect(page.getByRole("link", { name: "Back to projects" })).toBeVisible();
    // The application frame stays usable from a 404.
    await expect(page.getByRole("navigation", { name: "Application navigation" })).toBeVisible();
  });

  test("the 404 page links back to a working route", async ({ page }) => {
    await seedSession(page);
    await mockApi(page);
    await page.goto("/nope");

    await page.getByRole("link", { name: "Back to projects" }).click();

    await expect(page).toHaveURL(/\/projects$/);
    await expect(page.getByRole("heading", { name: "Проекты", level: 1 })).toBeVisible();
  });

  test("an unknown nested project route renders the project-scope 404", async ({ page }) => {
    await seedSession(page);
    await mockApi(page);
    await page.goto("/projects/project-e2e/no-such-panel");

    // `ProjectLayout` must load the project before the nested `*` route can render,
    // so this also asserts the layout does not swallow unknown children.
    await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
    await expect(page.getByText("/projects/project-e2e/no-such-panel")).toBeVisible();
  });

  test("the index redirect reaches /projects", async ({ page }) => {
    await seedSession(page);
    await mockApi(page);
    await page.goto("/");

    await expect(page).toHaveURL(/\/projects$/);
    await expect(page.getByRole("heading", { name: "Проекты", level: 1 })).toBeVisible();
  });
});
