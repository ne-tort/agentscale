/**
 * Live-layer helpers: proxy the SPA's `/api/v1/**` calls to a deployed API and
 * perform a real sign-in.
 *
 * `route.fetch()` issues the upstream request from the Playwright process, not the
 * browser, so the layer does not depend on the API's CORS policy for an arbitrary
 * preview origin. Headers are copied back verbatim, which keeps JSON bodies and
 * status codes exact; the only thing dropped is the CORS preflight concern, not
 * the API contract under test.
 */
import { expect, type Page, type Route } from "@playwright/test";

export interface LiveCredentials {
  readonly apiUrl: string;
  readonly username: string;
  readonly password: string;
}

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

/**
 * Proxies every `/api/v1/**` browser request to `apiUrl`.
 * `apiUrl` may be given with or without the `/api/v1` suffix.
 */
export async function installApiProxy(page: Page, apiUrl: string): Promise<void> {
  const base = trimTrailingSlash(apiUrl);
  const root = /\/api\/v1$/.test(base) ? base.slice(0, -"/api/v1".length) : base;

  await page.route(`**/api/v1/**`, async (route: Route) => {
    const incoming = new URL(route.request().url());
    const target = `${root}${incoming.pathname}${incoming.search}`;
    try {
      const response = await route.fetch({
        url: target,
        timeout: 60_000,
        // Never follow to another host implicitly: the API must answer where it is.
        maxRedirects: 0,
      });
      await route.fulfill({ response });
    } catch (error) {
      await route.abort(error instanceof Error ? "failed" : "failed");
    }
  });
}

/** Signs in through the real form and waits for the authenticated shell. */
export async function signIn(page: Page, credentials: LiveCredentials): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email or username").fill(credentials.username);
  await page.getByLabel("Password").fill(credentials.password);
  await page.getByRole("button", { name: "Sign in" }).click();

  // Sign-in resolves through `/auth/login` and then `/me`. A failure keeps the user
  // on /login with a `role="alert"`; surfacing that message makes a credential or
  // permission problem readable instead of a bare timeout.
  const alert = page.getByRole("alert");
  await expect(alert.or(page.getByRole("button", { name: "Sign out" }))).toBeVisible({ timeout: 30_000 });
  if (await alert.isVisible()) {
    const message = await alert.innerText();
    throw new Error(`Live sign-in failed for "${credentials.username}": ${message}`);
  }
}
