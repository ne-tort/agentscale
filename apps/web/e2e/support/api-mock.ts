/**
 * `page.route()` doubles for the smoke layer.
 *
 * The SPA calls `/api/v1/**` on its own origin (the client base URL is `/api/v1`),
 * so every request is interceptable without CORS tricks. The helper installs a
 * catch-all first and lets individual tests override specific paths: Playwright
 * matches routes in reverse registration order, so a later `page.route()` wins.
 */
import type { Page, Route } from "@playwright/test";

import {
  currentUserResponse,
  E2E_CABINETS,
  E2E_PROJECTS,
  E2E_SESSION,
  projectFixture,
  STORAGE_KEY,
} from "./fixtures";

export const API_PREFIX = "/api/v1";

export type ApiMode = "healthy" | "offline" | "server-error";

export interface ApiMockOptions {
  /**
   * - `healthy` — answers the endpoints the smoke flows touch.
   * - `offline` — aborts every request, i.e. the API host is unreachable.
   * - `server-error` — answers 500 with the API error envelope.
   */
  mode?: ApiMode;
  /** Extra per-path handlers, keyed by a path suffix such as `/me`. */
  overrides?: Record<string, (route: Route) => Promise<void> | void>;
}

function json(route: Route, body: unknown, status = 200): Promise<void> {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

function apiError(route: Route, status: number, code: string, detail: string): Promise<void> {
  return json(route, { code, title: "Request failed", status, detail }, status);
}

function pathOf(route: Route): string {
  return new URL(route.request().url()).pathname;
}

/** Request body of a routed call, or `undefined` for GETs. */
export function requestBody(route: Route): Record<string, unknown> | undefined {
  const raw = route.request().postData();
  if (!raw) return undefined;
  try {
    return JSON.parse(raw) as Record<string, unknown>;
  } catch {
    return undefined;
  }
}

/**
 * Installs the catch-all API double. Call it before `page.goto()`.
 * Returns a function that removes the mock (handy when a test switches modes).
 */
export async function mockApi(page: Page, options: ApiMockOptions = {}): Promise<() => Promise<void>> {
  const { mode = "healthy", overrides = {} } = options;

  const handler = async (route: Route): Promise<void> => {
    const path = pathOf(route);
    const suffix = Object.keys(overrides).find((key) => path.endsWith(key));
    if (suffix) {
      await overrides[suffix](route);
      return;
    }

    if (mode === "offline") {
      await route.abort("failed");
      return;
    }
    if (mode === "server-error") {
      await apiError(route, 500, "INTERNAL_ERROR", "The API failed to answer (mocked 500).");
      return;
    }

    if (path === `${API_PREFIX}/auth/login`) {
      await json(route, E2E_SESSION);
      return;
    }
    if (path === `${API_PREFIX}/auth/logout`) {
      await route.fulfill({ status: 204, body: "" });
      return;
    }
    if (path === `${API_PREFIX}/auth/refresh`) {
      await json(route, E2E_SESSION);
      return;
    }
    if (path === `${API_PREFIX}/me`) {
      await json(route, currentUserResponse());
      return;
    }
    if (path === `${API_PREFIX}/cabinets`) {
      await json(route, { items: E2E_CABINETS });
      return;
    }
    if (/^\/api\/v1\/cabinets\/[^/]+\/projects$/.test(path)) {
      await json(route, route.request().method() === "POST" ? projectFixture() : { items: E2E_PROJECTS });
      return;
    }
    if (/^\/api\/v1\/cabinets\/[^/]+\/me\/selection$/.test(path)) {
      await json(route, { cabinet_id: E2E_CABINETS[0].id, project_id: null });
      return;
    }
    if (/^\/api\/v1\/projects\/[^/]+$/.test(path)) {
      const requested = path.split("/").pop() ?? "project-e2e";
      const known = E2E_PROJECTS.find((project) => project.id === requested);
      await json(route, known ?? projectFixture({ id: requested }));
      return;
    }

    // Unmocked endpoint: fail loudly instead of silently returning an empty body,
    // so a screen that starts calling a new endpoint cannot drift out of coverage.
    await apiError(route, 404, "E2E_UNMOCKED_ENDPOINT", `No E2E mock for ${route.request().method()} ${path}`);
  };

  await page.route(`**${API_PREFIX}/**`, handler);
  return async () => {
    await page.unroute(`**${API_PREFIX}/**`, handler);
  };
}

/**
 * Seeds a prior session into `localStorage` before the first script runs.
 * `context.addInitScript` survives navigations, unlike `page.evaluate` after load.
 *
 * `AuthLifecycle` only restores a session it can validate through `/me`, so the
 * API double must still answer `/me` for the seeded session to stick.
 */
export async function seedSession(page: Page): Promise<void> {
  await page.addInitScript(
    ([key, tokens]) => {
      window.localStorage.setItem(key, JSON.stringify(tokens));
    },
    [STORAGE_KEY, E2E_SESSION] as const,
  );
}

/** Clears any persisted session so a test starts unauthenticated. */
export async function clearSession(page: Page): Promise<void> {
  await page.addInitScript(([key]) => {
    window.localStorage.removeItem(key);
  }, [STORAGE_KEY] as const);
}
