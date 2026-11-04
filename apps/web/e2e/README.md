# Browser E2E (`apps/web/e2e`)

End-to-end tests that drive a real Chromium against the built SPA. They exist
because nothing else in the repo opens a browser: `vitest` runs the React tree in
jsdom with mocked `fetch`, and `apps/api/tests/integration` only speaks HTTP. A
broken router, a thrown render error, a missing form control or an unhandled API
failure is invisible to both — and shows up as a white page for a user.

```
e2e/
  smoke/          mocked API, no backend required — always runs
  live/           real API behind E2E_API_URL — skipped when unset
  support/        route doubles, fixtures, live proxy/sign-in helpers
```

## Quick start

```powershell
cd apps/web
npm run test:e2e:install   # one-time: downloads Chromium only
npm run test:e2e           # smoke layer (mocked API)
```

The suite builds the bundle and serves it with `vite preview` automatically via
`webServer` in `playwright.config.ts`; there is no separate server to start.
Direct invocations work the same way:

```powershell
npx playwright test --project=chromium
npx playwright test --project=chromium --ui          # watch/debug mode
npx playwright test --project=chromium e2e/smoke/login.spec.ts
npx playwright test --project=chromium --headed      # see the browser
```

## Projects and ports

Only **`chromium`** is configured. Firefox and WebKit binaries are never
downloaded — spinning up three engines tripled install time and CI minutes for no
coverage the smoke layer lacks. `--project=chromium` is therefore the only target
and every script passes it.

`webServer` runs `vite build && vite preview` on **`127.0.0.1:4173`** (override the
port with `E2E_PREVIEW_PORT`, which moves `baseURL` with it). `preview` is used
rather than `dev` because it serves the production artifact: hashed asset names,
the SPA history fallback and no HMR client. `--host 127.0.0.1` is explicit — a bare
`vite preview` binds `localhost`, which resolves to `::1` on Windows while
`baseURL` is IPv4, and Playwright would then time out waiting for the server.

`build:e2e` runs `vite build` only, **not** `npm run build`: that wrapper also runs
`tsc --noEmit` over the whole app, which is a separate gate (and currently red in
unrelated zones). If the bundle cannot be produced, the E2E layer cannot start —
that is the intended behaviour, because a bundle is what the browser gets.

## The two layers

### `smoke/` — mocked API, always green

Every `/api/v1/**` request is intercepted with `page.route()`; see
`support/api-mock.ts`. No backend, no database, no credentials, works offline (the
bundle is built locally). It covers:

| File | What it proves |
|------|----------------|
| `login.spec.ts` | `/login` renders both credential fields; an unreachable API and an API 500 both produce a `role="alert"` and keep the user on the route (never a blank page); a successful sign-in leaves `/login`; `?next=//evil.example` is not followed. |
| `protected-routes.spec.ts` | A protected route without a session redirects to `/login?next=…` with the full path preserved; the redirect is honoured after sign-in; a persisted session restores through `/me`; a session the API rejects (401) is dropped from `localStorage`; sign-out clears the session. |
| `not-found.spec.ts` | Anonymous unknown paths redirect to `/login` instead of rendering the shell; the authenticated 404 renders inside the frame with a working way back; inside a project the shell's own route-recovery panel handles unknown sections; a project the API refuses shows a retryable error. |
| `workspace.spec.ts` | The projects workspace paints its cabinet selector, project rows, status labels and lifecycle actions; an offline API degrades to a retryable alert with the frame intact; the dynamic-module gate fails closed without sending a registry request; no uncaught `pageerror` on the surfaces under test. |

The smoke layer asserts *rendered structure*, not styling. It deliberately does
not assert pixel output, so CSS changes do not turn the suite red.

### `live/` — real API

Skipped with an explicit reason unless **all three** variables are set:

| Variable | Meaning |
|----------|---------|
| `E2E_API_URL` | Base URL of a deployed API, e.g. `http://172.31.156.203:8088/api/v1` (`/api/v1` optional) |
| `E2E_USERNAME` | Login of an **employee** account on that deployment |
| `E2E_PASSWORD` | Its password |

```powershell
$env:E2E_API_URL   = "http://172.31.156.203:8088/api/v1"
$env:E2E_USERNAME  = "<employee login>"
$env:E2E_PASSWORD  = "<password>"
npm run test:e2e:live
```

What the live layer adds over the mocks — it is the only place where the real wire
shapes are exercised:

- `POST /auth/login` returns the exact `{access_token, refresh_token}` shape the
  client persists;
- the real `/me` body satisfies `normalizeCurrentUser()` (`active`, `roles`,
  `contours`, identity);
- the session survives a reload through `/me`, and sign-out actually revokes;
- `GET /cabinets`, `GET /cabinets/{id}/projects` and the project lifecycle enum
  pass `mapProject()`'s strict validation (unknown statuses and foreign cabinets are
  rejected loudly);
- the dynamic-module registry route answers with a contract state.

Browser calls are proxied through `route.fetch()` from the Playwright process, so
the run does not depend on the deployed API's CORS policy for the local preview
origin. The API contract under test is unchanged — only the transport hop differs.

**No test user is invented.** There is no fixture that can mint a password login in
this repo (`AUTH_MODE=test` mints HS256 tokens, but `POST /auth/login` answers
`503 AUTH_MISCONFIGURED` in that mode, and browser sessions need a real login), so
the live layer takes credentials from the environment instead of hard-coding a
seed account. Point it at a real employee login on a dev deployment.

If you provision a throwaway account for CI, create it through the documented
platform-admin → company → employee-invitation flow and read
`keycloak_sub`/`login` from that response rather than inserting rows by hand. Keep
the password in the CI secret store only.

## CI

```yaml
- run: npm run test:e2e:install      # Chromium only
- run: npm run test:e2e              # smoke; needs no services
```

`CI=true` flips three config values: two retries instead of zero, one worker per
retry attempt capped at two workers, and an HTML report alongside the list output.
`trace: on-first-retry` means a trace is captured only for a retried (i.e. failing)
test, so green runs stay cheap; artifacts land in `test-results/`.

Do not add `@live` tests to the default CI job — they need a reachable deployment
and a real account. Run them as a separate, explicitly scheduled job that exports
the three variables, so a missing secret produces a skip rather than a red gate.

## Conventions

- Files are `*.spec.ts`; new smoke coverage goes in `e2e/smoke/`, live coverage in
  `e2e/live/` and carries `@live` in its `test.describe` title (that tag is what the
  npm scripts filter on).
- Always install the API double **before** `page.goto()`, otherwise the first
  requests escape to the network.
- Change `mockApi()`'s fixture shapes when the API contracts change — an unmocked
  endpoint answers `404 E2E_UNMOCKED_ENDPOINT` on purpose, so new calls surface as a
  failure instead of silently returning `undefined`.
- Prefer role/label locators (`getByRole`, `getByLabel`) over CSS classes; the CSS
  classes are asserted only where they are the contract (`.projects-name`,
  `.employee-cabinet-list`).
- A test that needs a signed-in session uses `seedSession(page)` plus an API double
  that still answers `/me`; `AuthLifecycle` rejects a session it cannot validate.
- When a screen legitimately refuses an operation (missing capability, missing
  transport), assert the refusal and the reason — do not weaken the test to make a
  future implementation pass.
