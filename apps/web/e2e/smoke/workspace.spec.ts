import { expect, type Page, test } from "@playwright/test";

import { mockApi, seedSession } from "../support/api-mock";

/**
 * Smoke layer 4 — the authenticated surfaces a browser user actually lands on.
 *
 * These assert rendered structure, not styling: which heading paints, whether a
 * loading state resolves, and whether a failed request degrades to a message.
 * A React render exception would leave `#root` empty here and pass every jsdom test,
 * so the common suite also fails on uncaught page errors.
 */

/** Collects uncaught render/runtime errors — the "white screen" class of defect. */
function collectPageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  return errors;
}

test.describe("authenticated surfaces", () => {
  test.beforeEach(async ({ page }) => {
    await seedSession(page);
  });

  test("the projects workspace renders its cabinet selector and project list", async ({ page }) => {
    const errors = collectPageErrors(page);
    await mockApi(page);
    await page.goto("/projects");

    await expect(page.getByRole("heading", { name: "Проекты", level: 1 })).toBeVisible();
    await expect(page.getByLabel("Выбрать кабинет")).toHaveValue("cabinet-e2e");
    await expect(page.getByRole("listitem")).toHaveCount(2);
    await expect(page.getByRole("link", { name: "E2E Project" })).toHaveAttribute("href", "/projects/project-e2e");
    await expect(page.getByRole("link", { name: "E2E Active Project" })).toBeVisible();

    // Statuses are rendered through the shared label map, not raw enum values.
    // Scope to the row: the filter dropdown also contains these labels.
    const draftRow = page.getByRole("listitem").filter({ hasText: "project-e2e" }).first();
    await expect(draftRow).toContainText("Черновик");
    const activeRow = page.getByRole("listitem").filter({ hasText: "project-e2e-active" });
    await expect(activeRow).toContainText("Активен");
    // Lifecycle affordances follow the status: a draft can launch, an active one cannot.
    await expect(draftRow.getByRole("button", { name: "Запустить" })).toBeVisible();
    await expect(activeRow.getByRole("button", { name: "Приостановить" })).toBeVisible();

    expect(errors, `uncaught page errors: ${errors.join(" | ")}`).toEqual([]);
  });

  test("an unreachable API on /projects shows a retryable error, not a blank frame", async ({ page }) => {
    await mockApi(page, { mode: "offline", keepSession: true });
    await page.goto("/projects");

    const alert = page.getByRole("alert").filter({ hasText: "Не удалось загрузить кабинеты" });
    await expect(alert).toBeVisible();
    await expect(alert.getByRole("button", { name: "Повторить" })).toBeVisible();
    // The frame itself must survive the failure.
    await expect(page.getByRole("heading", { name: "Проекты", level: 1 })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Application navigation" })).toBeVisible();
  });

  test("dynamic modules deny access without requesting the registry", async ({ page }) => {
    const requests: string[] = [];
    page.on("request", (request) => {
      const path = new URL(request.url()).pathname;
      if (path.startsWith("/api/v1/")) requests.push(path);
    });
    await mockApi(page);
    await page.goto("/dynamic-modules");

    // The shell mounts no `CapabilityProvider` (see `src/features/dynamic-modules/access.tsx`),
    // so the gate fails closed with `capability_context_unavailable`. Asserting the
    // refusal — not the definition table — is what keeps this honest today.
    const denial = page.getByRole("alert").filter({ hasText: "Capability required" });
    await expect(denial).toBeVisible();
    await expect(denial).toContainText("platform.queries");
    await expect(denial).toContainText("Nothing was requested from the server.");

    // `/me` is the session probe from `AuthLifecycle`; nothing else may be fetched.
    expect(requests.filter((path) => path !== "/api/v1/me")).toEqual([]);
  });

  test("the unknown-route surface keeps the shell navigable", async ({ page }) => {
    const errors = collectPageErrors(page);
    await mockApi(page);
    await page.goto("/dynamic-modules/no-such-definition");

    // A definition id that the registry never confirms must not render an empty page;
    // the route exists, so the screen owns the failure state.
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    expect(errors, `uncaught page errors: ${errors.join(" | ")}`).toEqual([]);
  });
});
