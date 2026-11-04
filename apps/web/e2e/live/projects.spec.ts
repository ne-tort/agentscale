import { expect, test } from "@playwright/test";

import { E2E_LIVE_CREDENTIALS, LIVE_SKIP_REASON } from "../support/live-config";
import { installApiProxy, signIn } from "../support/live";

/**
 * Live layer — the project list and the dynamic-module registry against a real API.
 *
 * Both screens validate server responses strictly (`mapProject()` rejects unknown
 * lifecycle values and foreign cabinets; the registry gate validates grants), so a
 * green run here means the deployed API still satisfies the client's contracts —
 * something no mocked test can prove.
 */
test.describe("live project list @live", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!E2E_LIVE_CREDENTIALS, LIVE_SKIP_REASON);
    await installApiProxy(page, E2E_LIVE_CREDENTIALS!.apiUrl);
    await signIn(page, E2E_LIVE_CREDENTIALS!);
  });

  test("the projects screen resolves a cabinet and reports no contract error", async ({ page }) => {
    await page.goto("/projects");

    await expect(page.getByRole("heading", { name: "Проекты", level: 1 })).toBeVisible({ timeout: 30_000 });

    // Positive outcome: a cabinet was resolved and the project list (possibly empty)
    // is rendered. Negative outcome: a `role="alert"` carries a contract/transport
    // message, which must fail the test rather than be tolerated.
    const noCabinets = page.getByText("Нет доступных кабинетов");
    const list = page.getByRole("region", { name: "Список проектов" });

    await expect(list.or(noCabinets)).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("alert")).toHaveCount(0);
    // A resolved cabinet must be selectable; an unresolved one is the empty state above.
    if (await list.isVisible()) {
      await expect(page.getByLabel("Выбрать кабинет")).toBeVisible();
      await expect(page.getByRole("region", { name: "Сводка проектов" })).toBeVisible();
    }
  });

  test("opening a project reaches a workspace or a readable failure, never a blank page", async ({ page }) => {
    await page.goto("/projects");
    await expect(page.getByRole("heading", { name: "Проекты", level: 1 })).toBeVisible({ timeout: 30_000 });

    const projectLink = page.locator(".projects-name").first();
    if (await projectLink.count() === 0) test.skip(true, "the live account owns no projects to open");

    await projectLink.click();

    // Either the workspace shell mounts, or the project is refused with an alert
    // that explains why. An empty `#root` would satisfy neither.
    const shell = page.getByRole("navigation", { name: "Workspace sections" });
    const failure = page.getByRole("alert");
    await expect(shell.or(failure)).toBeVisible({ timeout: 30_000 });
    if (await failure.count() > 0) {
      // A refusal is acceptable only when it states a reason.
      await expect(failure.first()).not.toBeEmpty();
    }
  });
});

test.describe("live dynamic modules @live", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!E2E_LIVE_CREDENTIALS, LIVE_SKIP_REASON);
    await installApiProxy(page, E2E_LIVE_CREDENTIALS!.apiUrl);
    await signIn(page, E2E_LIVE_CREDENTIALS!);
  });

  test("the registry route answers with a contract state, not an unhandled error", async ({ page }) => {
    await page.goto("/dynamic-modules");

    await expect(page.getByRole("heading", { name: "Dynamic modules", level: 1 })).toBeVisible({ timeout: 30_000 });

    // The shell mounts no capability provider, so the gate fails closed before any
    // request. That refusal is the current as-built contract; if a host provider is
    // added later, the registry table will appear instead and this assertion must
    // be updated to assert the table. Both are "answered", neither is a blank page.
    const denial = page.getByRole("alert").filter({ hasText: "Capability required" });
    const table = page.getByRole("table");
    const apiNotice = page.getByText("Dynamic module API is not wired in this shell build");

    await expect(denial.or(table).or(apiNotice)).toBeVisible({ timeout: 30_000 });
    if (await denial.isVisible()) {
      await expect(denial).toContainText("platform.queries");
    }
  });

  test("the cabinets screen lists real cabinets from the API", async ({ page }) => {
    await page.goto("/cabinets");

    await expect(page.getByRole("heading", { name: "Cabinets", level: 1 })).toBeVisible({ timeout: 30_000 });
    // Either cabinets render as links, or the empty state explains the account has none.
    const cabinetLink = page.locator(".employee-cabinet-list a").first();
    const empty = page.getByText("Для этого сотрудника нет доступных кабинетов.");
    await expect(cabinetLink.or(empty)).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("alert")).toHaveCount(0);
  });
});
