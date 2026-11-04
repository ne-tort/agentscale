# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: smoke\protected-routes.spec.ts >> protected routes >> signing out returns to /login and clears the stored session
- Location: e2e\smoke\protected-routes.spec.ts:80:3

# Error details

```
Error: expect(page).toHaveURL(expected) failed

Expected pattern: /\/login$/
Received string:  "http://127.0.0.1:4173/login?next=%2Fcabinets"
Timeout: 7000ms

Call log:
  - Expect "toHaveURL" with timeout 7000ms
    18 × locator resolved to <html lang="en">…</html>
       - unexpected value "http://127.0.0.1:4173/login?next=%2Fcabinets"

```

```yaml
- main:
  - paragraph: Employee workspace
  - heading "Sign in to agentscale" [level=1]
  - text: Email or username
  - textbox "Email or username"
  - text: Password
  - textbox "Password"
  - button "Sign in"
```

# Test source

```ts
  1  | import { expect, test } from "@playwright/test";
  2  | 
  3  | import { clearSession, mockApi, seedSession } from "../support/api-mock";
  4  | 
  5  | /**
  6  |  * Smoke layer 2 — route guarding.
  7  |  *
  8  |  * `RequireAuth` decides between a loading state, the shell and a redirect to
  9  |  * `/login?next=…`. That gate only exists at runtime: vitest renders providers with
  10 |  * hand-made context, so a broken `Navigate`/`Outlet` wiring would pass unit tests
  11 |  * and strand real users on a blank frame.
  12 |  */
  13 | test.describe("protected routes", () => {
  14 |   test.beforeEach(async ({ page }) => {
  15 |     await clearSession(page);
  16 |   });
  17 | 
  18 |   test("an anonymous visit to a protected route redirects to /login", async ({ page }) => {
  19 |     await mockApi(page);
  20 |     await page.goto("/projects");
  21 | 
  22 |     await expect(page).toHaveURL(/\/login\?next=%2Fprojects$/);
  23 |     await expect(page.getByRole("heading", { name: "Sign in to agentscale" })).toBeVisible();
  24 |     // The protected shell must not have painted behind the redirect.
  25 |     await expect(page.getByRole("heading", { name: "Проекты", level: 1 })).toHaveCount(0);
  26 |   });
  27 | 
  28 |   test("the redirect keeps the requested path in ?next= and honours it after sign-in", async ({ page }) => {
  29 |     await mockApi(page);
  30 |     await page.goto("/cabinets");
  31 | 
  32 |     await expect(page).toHaveURL(/\/login\?next=%2Fcabinets$/);
  33 | 
  34 |     await page.getByLabel("Email or username").fill("e2e@example.test");
  35 |     await page.getByLabel("Password").fill("secret");
  36 |     await page.getByRole("button", { name: "Sign in" }).click();
  37 | 
  38 |     await expect(page).toHaveURL(/\/cabinets$/);
  39 |     await expect(page.getByRole("heading", { name: "Cabinets", level: 1 })).toBeVisible();
  40 |   });
  41 | 
  42 |   test("a nested protected route redirects with its full path", async ({ page }) => {
  43 |     await mockApi(page);
  44 |     await page.goto("/projects/project-e2e/files");
  45 | 
  46 |     await expect(page).toHaveURL(/\/login\?next=%2Fprojects%2Fproject-e2e%2Ffiles$/);
  47 |   });
  48 | 
  49 |   test("a persisted session restores without a redirect", async ({ page }) => {
  50 |     await mockApi(page);
  51 |     await seedSession(page);
  52 |     await page.goto("/cabinets");
  53 | 
  54 |     // `AuthLifecycle.recover()` validates the stored tokens through `/me`.
  55 |     await expect(page).toHaveURL(/\/cabinets$/);
  56 |     await expect(page.getByRole("heading", { name: "Cabinets", level: 1 })).toBeVisible();
  57 |     await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  58 |   });
  59 | 
  60 |   test("a session the API rejects is dropped and the user is sent back to /login", async ({ page }) => {
  61 |     await mockApi(page, {
  62 |       overrides: {
  63 |         "/me": async (route) => {
  64 |           await route.fulfill({
  65 |             status: 401,
  66 |             contentType: "application/json",
  67 |             body: JSON.stringify({ code: "UNAUTHORIZED", title: "Unauthorized", status: 401 }),
  68 |           });
  69 |         },
  70 |       },
  71 |     });
  72 |     await seedSession(page);
  73 |     await page.goto("/projects");
  74 | 
  75 |     await expect(page).toHaveURL(/\/login\?next=%2Fprojects$/);
  76 |     // The rejected credentials must not survive the reload.
  77 |     await expect.poll(async () => page.evaluate(() => window.localStorage.getItem("agentscale.web.session"))).toBeNull();
  78 |   });
  79 | 
  80 |   test("signing out returns to /login and clears the stored session", async ({ page }) => {
  81 |     await mockApi(page);
  82 |     await seedSession(page);
  83 |     await page.goto("/cabinets");
  84 |     await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  85 | 
  86 |     await page.getByRole("button", { name: "Sign out" }).click();
  87 | 
> 88 |     await expect(page).toHaveURL(/\/login$/);
     |                        ^ Error: expect(page).toHaveURL(expected) failed
  89 |     await expect.poll(async () => page.evaluate(() => window.localStorage.getItem("agentscale.web.session"))).toBeNull();
  90 |   });
  91 | });
  92 | 
```