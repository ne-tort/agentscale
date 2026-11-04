# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: smoke\not-found.spec.ts >> unknown routes >> an unknown nested project route renders the project-scope 404
- Location: e2e\smoke\not-found.spec.ts:47:3

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByRole('heading', { name: 'Page not found' })
Expected: visible
Timeout: 7000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" getByRole('heading', { name: 'Page not found' }) with timeout 7000ms
  - waiting for getByRole('heading', { name: 'Page not found' })

```

```yaml
- alert:
  - heading "Проект недоступен" [level=1]
  - paragraph: No E2E mock for GET /api/v1/cabinets/cabinet-e2e
  - button "Повторить"
  - link "Вернуться к проектам":
    - /url: /projects
  - button "Sign out"
```

# Test source

```ts
  1  | import { expect, test } from "@playwright/test";
  2  | 
  3  | import { clearSession, mockApi, seedSession } from "../support/api-mock";
  4  | 
  5  | /**
  6  |  * Smoke layer 3 — unknown routes.
  7  |  *
  8  |  * Two distinct surfaces must answer a bad path:
  9  |  *  - anonymous: the auth gate wins, the URL is preserved in `?next=` and the 404
  10 |  *    body is never rendered (otherwise a 404 would leak the shell's navigation);
  11 |  *  - authenticated: `NotFoundPage` renders inside the frame with a way back.
  12 |  */
  13 | test.describe("unknown routes", () => {
  14 |   test("an anonymous unknown path redirects to /login and keeps the path", async ({ page }) => {
  15 |     await clearSession(page);
  16 |     await mockApi(page);
  17 |     await page.goto("/definitely/not/a/route");
  18 | 
  19 |     await expect(page).toHaveURL(/\/login\?next=%2Fdefinitely%2Fnot%2Fa%2Froute$/);
  20 |     await expect(page.getByRole("heading", { name: "Sign in to agentscale" })).toBeVisible();
  21 |   });
  22 | 
  23 |   test("an authenticated unknown path renders the 404 page inside the shell", async ({ page }) => {
  24 |     await seedSession(page);
  25 |     await mockApi(page);
  26 |     await page.goto("/definitely/not/a/route");
  27 | 
  28 |     await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
  29 |     // The detail echoes the attempted path, proving the router matched the catch-all.
  30 |     await expect(page.getByText("/definitely/not/a/route")).toBeVisible();
  31 |     await expect(page.getByRole("link", { name: "Back to projects" })).toBeVisible();
  32 |     // The application frame stays usable from a 404.
  33 |     await expect(page.getByRole("navigation", { name: "Application navigation" })).toBeVisible();
  34 |   });
  35 | 
  36 |   test("the 404 page links back to a working route", async ({ page }) => {
  37 |     await seedSession(page);
  38 |     await mockApi(page);
  39 |     await page.goto("/nope");
  40 | 
  41 |     await page.getByRole("link", { name: "Back to projects" }).click();
  42 | 
  43 |     await expect(page).toHaveURL(/\/projects$/);
  44 |     await expect(page.getByRole("heading", { name: "Проекты", level: 1 })).toBeVisible();
  45 |   });
  46 | 
  47 |   test("an unknown nested project route renders the project-scope 404", async ({ page }) => {
  48 |     await seedSession(page);
  49 |     await mockApi(page);
  50 |     await page.goto("/projects/project-e2e/no-such-panel");
  51 | 
  52 |     // `ProjectLayout` must load the project before the nested `*` route can render,
  53 |     // so this also asserts the layout does not swallow unknown children.
> 54 |     await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
     |                                                                         ^ Error: expect(locator).toBeVisible() failed
  55 |     await expect(page.getByText("/projects/project-e2e/no-such-panel")).toBeVisible();
  56 |   });
  57 | 
  58 |   test("the index redirect reaches /projects", async ({ page }) => {
  59 |     await seedSession(page);
  60 |     await mockApi(page);
  61 |     await page.goto("/");
  62 | 
  63 |     await expect(page).toHaveURL(/\/projects$/);
  64 |     await expect(page.getByRole("heading", { name: "Проекты", level: 1 })).toBeVisible();
  65 |   });
  66 | });
  67 | 
```