import { expect, test } from "@playwright/test";

import { mockApi, clearSession } from "../support/api-mock";

/**
 * Smoke layer 1 — the login surface.
 *
 * Every assertion here is about what the browser *renders*, which is exactly what
 * the jsdom/vitest suite cannot prove: a blank page, a thrown render error or a
 * missing form control of a mounted SPA.
 *
 * The API is fully mocked through `page.route()`, so this file must stay green on
 * any machine, with or without a backend.
 */
test.describe("login surface", () => {
  test.beforeEach(async ({ page }) => {
    await clearSession(page);
  });

  test("renders the sign-in form with both credential fields", async ({ page }) => {
    await mockApi(page);
    await page.goto("/login");

    await expect(page.getByRole("heading", { name: "Sign in to agentscale" })).toBeVisible();
    await expect(page.getByLabel("Email or username")).toBeVisible();
    await expect(page.getByLabel("Password")).toBeVisible();

    // The password field must stay masked, and the form must submit through a button.
    await expect(page.getByLabel("Password")).toHaveAttribute("type", "password");
    await expect(page.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });

  test("an unreachable API shows an error instead of a blank page", async ({ page }) => {
    await mockApi(page, { mode: "offline" });
    await page.goto("/login");

    await page.getByLabel("Email or username").fill("e2e@example.test");
    await page.getByLabel("Password").fill("not-the-password");
    await page.getByRole("button", { name: "Sign in" }).click();

    // `LoginPage` surfaces the failure in a `role="alert"` paragraph; the form and
    // its controls stay mounted so the user can retry without reloading.
    const alert = page.getByRole("alert");
    await expect(alert).toBeVisible();
    await expect(alert).not.toBeEmpty();

    // A failed sign-in must not navigate away from the login route.
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole("button", { name: "Sign in" })).toBeEnabled();
    await expect(page.getByRole("heading", { name: "Sign in to agentscale" })).toBeVisible();
  });

  test("an API 500 during sign-in is reported and keeps the user on /login", async ({ page }) => {
    await mockApi(page, { mode: "server-error" });
    await page.goto("/login");

    await page.getByLabel("Email or username").fill("e2e@example.test");
    await page.getByLabel("Password").fill("secret");
    await page.getByRole("button", { name: "Sign in" }).click();

    await expect(page.getByRole("alert")).toContainText("mocked 500");
    await expect(page).toHaveURL(/\/login$/);
  });

  test("a successful sign-in leaves /login for the workspace", async ({ page }) => {
    await mockApi(page);
    await page.goto("/login");

    await page.getByLabel("Email or username").fill("e2e@example.test");
    await page.getByLabel("Password").fill("secret");
    await page.getByRole("button", { name: "Sign in" }).click();

    // `/projects` needs the cabinets + projects doubles from `mockApi`.
    await expect(page).toHaveURL(/\/projects/);
    await expect(page.getByRole("heading", { name: "Проекты", level: 1 })).toBeVisible();
  });

  test("an open redirect target in ?next= is ignored", async ({ page }) => {
    await mockApi(page);
    await page.goto("/login?next=//evil.example.test/steal");

    await page.getByLabel("Email or username").fill("e2e@example.test");
    await page.getByLabel("Password").fill("secret");
    await page.getByRole("button", { name: "Sign in" }).click();

    // `getSafeNextPath()` rejects protocol-relative targets, so the fallback applies.
    await expect(page).toHaveURL(/\/projects$/);
  });
});
