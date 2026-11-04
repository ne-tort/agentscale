# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: live\auth.spec.ts >> live sign-in @live >> a real employee signs in and reaches the authenticated frame
- Location: e2e\live\auth.spec.ts:21:3

# Error details

```
Error: Live sign-in failed for "e2e-user": Failed to fetch
```

# Page snapshot

```yaml
- main [ref=e3]:
  - generic [ref=e4]:
    - paragraph [ref=e5]: Employee workspace
    - heading "Sign in to agentscale" [level=1] [ref=e6]
    - generic [ref=e7]:
      - generic [ref=e8]:
        - text: Email or username
        - textbox "Email or username" [ref=e9]: e2e-user
      - generic [ref=e10]:
        - text: Password
        - textbox "Password" [ref=e11]: e2e-pass
      - alert [ref=e12]: Failed to fetch
      - button "Sign in" [ref=e13] [cursor=pointer]
```

# Test source

```ts
  1  | /**
  2  |  * Live-layer helpers: proxy the SPA's `/api/v1/**` calls to a deployed API and
  3  |  * perform a real sign-in.
  4  |  *
  5  |  * `route.fetch()` issues the upstream request from the Playwright process, not the
  6  |  * browser, so the layer does not depend on the API's CORS policy for an arbitrary
  7  |  * preview origin. Headers are copied back verbatim, which keeps JSON bodies and
  8  |  * status codes exact; the only thing dropped is the CORS preflight concern, not
  9  |  * the API contract under test.
  10 |  */
  11 | import { expect, type Page, type Route } from "@playwright/test";
  12 | 
  13 | export interface LiveCredentials {
  14 |   readonly apiUrl: string;
  15 |   readonly username: string;
  16 |   readonly password: string;
  17 | }
  18 | 
  19 | function trimTrailingSlash(value: string): string {
  20 |   return value.replace(/\/+$/, "");
  21 | }
  22 | 
  23 | /**
  24 |  * Proxies every `/api/v1/**` browser request to `apiUrl`.
  25 |  * `apiUrl` may be given with or without the `/api/v1` suffix.
  26 |  */
  27 | export async function installApiProxy(page: Page, apiUrl: string): Promise<void> {
  28 |   const base = trimTrailingSlash(apiUrl);
  29 |   const root = /\/api\/v1$/.test(base) ? base.slice(0, -"/api/v1".length) : base;
  30 | 
  31 |   await page.route(`**/api/v1/**`, async (route: Route) => {
  32 |     const incoming = new URL(route.request().url());
  33 |     const target = `${root}${incoming.pathname}${incoming.search}`;
  34 |     try {
  35 |       const response = await route.fetch({
  36 |         url: target,
  37 |         timeout: 60_000,
  38 |         // Never follow to another host implicitly: the API must answer where it is.
  39 |         maxRedirects: 0,
  40 |       });
  41 |       await route.fulfill({ response });
  42 |     } catch (error) {
  43 |       await route.abort(error instanceof Error ? "failed" : "failed");
  44 |     }
  45 |   });
  46 | }
  47 | 
  48 | /** Signs in through the real form and waits for the authenticated shell. */
  49 | export async function signIn(page: Page, credentials: LiveCredentials): Promise<void> {
  50 |   await page.goto("/login");
  51 |   await page.getByLabel("Email or username").fill(credentials.username);
  52 |   await page.getByLabel("Password").fill(credentials.password);
  53 |   await page.getByRole("button", { name: "Sign in" }).click();
  54 | 
  55 |   // Sign-in resolves through `/auth/login` and then `/me`. A failure keeps the user
  56 |   // on /login with a `role="alert"`; surfacing that message makes a credential or
  57 |   // permission problem readable instead of a bare timeout.
  58 |   const alert = page.getByRole("alert");
  59 |   await expect(alert.or(page.getByRole("button", { name: "Sign out" }))).toBeVisible({ timeout: 30_000 });
  60 |   if (await alert.isVisible()) {
  61 |     const message = await alert.innerText();
> 62 |     throw new Error(`Live sign-in failed for "${credentials.username}": ${message}`);
     |           ^ Error: Live sign-in failed for "e2e-user": Failed to fetch
  63 |   }
  64 | }
  65 | 
```