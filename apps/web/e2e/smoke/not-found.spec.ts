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

  test("an unknown nested project route renders the shell's route-recovery surface", async ({ page }) => {
    await seedSession(page);
    await mockApi(page);
    await page.goto("/projects/project-e2e/no-such-panel");

    // Inside a project workspace the authority for "which sections exist" is
    // `WorkspaceShell.resolveWorkspaceRoute`, not the plain router: it renders
    // `children` only when the path matches a confirmed route, and otherwise
    // replaces the outlet with this recovery panel. The nested `*` route in
    // `ProjectLayout` therefore never paints — asserting `NotFoundPage` here
    // would be testing a surface the shell deliberately shadows.
    const recovery = page.getByRole("alert").filter({ hasText: "Unsupported workspace route" });
    await expect(recovery).toBeVisible();
    await expect(recovery.getByRole("heading", { name: "This route is not available in this workspace" })).toBeVisible();
    // The deep link is kept so the user can recover without losing it.
    await expect(page).toHaveURL(/\/projects\/project-e2e\/no-such-panel$/);
    await expect(recovery.getByText("/projects/project-e2e/no-such-panel")).toBeVisible();
    // Recovery must be one click, and the shell must stay navigable.
    await expect(recovery.getByRole("link", { name: "Open E2E Project" })).toHaveAttribute("href", "/projects/project-e2e");
    await expect(page.getByRole("navigation", { name: "Workspace sections" })).toBeVisible();
  });

  test("a project route that belongs to another project is refused, not silently loaded", async ({ page }) => {
    await seedSession(page);
    await mockApi(page, {
      overrides: {
        // The URL says `project-other`, but the shell was scoped to `project-e2e`
        // only if the layout loaded it — here the layout loads the requested id and
        // the shell must agree on scope rather than render foreign content.
        "/projects/project-other": async (route) => {
          await route.fulfill({
            status: 404,
            contentType: "application/json",
            body: JSON.stringify({ code: "NOT_FOUND", title: "Not found", status: 404, detail: "Проект не найден." }),
          });
        },
      },
    });
    await page.goto("/projects/project-other/runtime");

    const alert = page.getByRole("alert");
    await expect(alert.getByRole("heading", { name: "Проект недоступен" })).toBeVisible();
    await expect(alert.getByText("Проект не найден.")).toBeVisible();
    await expect(alert.getByRole("button", { name: "Повторить" })).toBeVisible();
    await expect(alert.getByRole("link", { name: "Вернуться к проектам" })).toBeVisible();
  });

  test("the index redirect reaches /projects", async ({ page }) => {
    await seedSession(page);
    await mockApi(page);
    await page.goto("/");

    await expect(page).toHaveURL(/\/projects$/);
    await expect(page.getByRole("heading", { name: "Проекты", level: 1 })).toBeVisible();
  });
});
