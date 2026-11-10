# Plugin module browser acceptance — NOT RUN

**Status: new harness source only. The tests were not executed. No Chromium,
HTTP environment, production CI, frontend/backend test, build, lint, compile,
network, Docker or git check was run while creating this harness. Production CI
evidence is pending; this directory is not live evidence and must not be used to
mark the plugin acceptance map green.**

This is an isolated Playwright acceptance layer for the real React shell, real
Chromium and real HTTPS browser/API/Gateway/static flows. It does not use jsdom,
API response doubles, a fake plugin iframe, a parent-app patch or a probe plugin.
TLS verification stays enabled. There are no default endpoints, accounts,
secrets, grants, installations, publications or artifact digests.

## Safety boundary and fixture contract

Use only a newly provisioned, isolated **disposable test environment**. Never
point these variables at prod, shared dev, a developer account or a company with
real data. `PLUGIN_ACCEPTANCE_REQUIRED=1` and
`PLUGIN_ACCEPTANCE_DISPOSABLE=1` are both mandatory. The configuration throws on
missing/invalid input instead of skipping. One worker and zero retries are
intentional. The reporter makes `collected=0` or any `skipped` result a process
failure.

Provision all of the following before a run:

1. Exact HTTPS web/shell, Gateway and separate static origins. `WEB_ORIGIN` must
   equal `SHELL_ORIGIN`. Gateway may equal web when both surfaces share an
   origin. Static must differ from both. TLS certificates must validate.
2. A platform-admin account and a disposable employee account. The employee is
   an active member of primary, expiring and revoke companies, but **not** of
   `WRONG_COMPANY_ID`. All five company IDs are distinct.
3. Three installations of the same signed private hello publication (primary,
   expiring and revoke), enabled with exact distinct durable instance/grant IDs.
   The primary grant outlives the complete run. The expiring grant expires more
   than 15 seconds after startup and within `EXPIRY_MAX_WAIT_MS`. The revoke
   installation is sacrificed only by the final test.
4. A **separate** signed disposable public publication, installation and grant.
   Its manifest and backend provide one unique anonymous read-only GET. Its
   `public_pages` contains both `PUBLIC_ROUTE_PATH` and the exact
   `PUBLIC_GET_PATH`; its sole public bootstrap operation is that GET.
   `PUBLIC_POST_PATH` must equal `PUBLIC_GET_PATH`, so the test proves that POST
   to the exact public operation is denied rather than testing an unrelated
   unknown route. `PUBLIC_PRIVATE_GET_PATH` is a different non-public operation.
   Its frontend performs canonical `plugin-rpc/1` and never downgrades to a
   private request. Public GET/HEAD return 200 with no session, Cookie or
   capability token. Explicitly supplied Cookie/Authorization/capability headers
   are tested as denial cases, not silently ignored.
5. Direct static GET/HEAD for both publications return the exact configured
   `X-AgentScale-Bundle-Digest`, exact shell-only `frame-ancestors`,
   `default-src 'self'; base-uri 'none'; object-src 'none'`, `nosniff`,
   `no-referrer` and the configured restrictive Permissions-Policy.

The stock hello manifest is not an anonymous-public success fixture: its
`list`/`greet` operations are private and its backend requires per-request
capability introspection. Do not weaken those assertions to make this harness
pass. Likewise, the acceptance scope assertion intentionally requires
`company_id`, `cabinet_id` and `project_id` from verified grant scope. At the
time this harness was written, the inspected bootstrap service returned only
`company_id`; that production gap is outside this directory and is not patched
or hidden here.

### Publication/digest rule

Every hello frontend/source change requires a newly built **and newly signed**
bundle digest and a new disposable publication. Update all affected installation
fixtures and env values together. This harness contains no production digest and
never certifies a previously published production digest as current. It checks
that admin installation, bootstrap, `iframe.name`, RPC envelopes and direct
static headers all agree on the explicitly supplied disposable digest.

No publication, install, grant, wake, uninstall or feature-flag mutation is
performed by the harness. Expected test effects are limited to real login,
read-only list/static requests, greet records in the disposable primary company,
and exactly one scoped `POST /admin/plugins/revoke` for the final dedicated
revoke fixture. The separate natural-expiry fixture is never revoked.
Each run needs fresh expiring/revoke fixtures; do not rerun against the consumed
state. There are no parent-app source changes, probe backends, server-side request
counters, database cleanup hooks or other probe side effects. The in-browser RPC
observer and listener add/remove counters are passive and bounded; negative tests
dispatch isolated `MessageEvent`s in Chromium without changing application files
or normal message delivery. Detached plugin Windows must be unusable.

When web and Gateway origins differ, Playwright forwards the browser's
`/api/v1/**` and `/pluginpage/**` requests with `route.fetch()` to the explicit
Gateway origin, preserving real HTTP request/response behavior and disabling
redirect-following. It never fabricates a response. Browser origins outside the
three configured origins, credential-like URL parameters and credentials on
anonymous direct-static requests fail the test. Browser contexts start with
explicit empty cookie/storage state; anonymous HTTP uses a separate
`credentials: "omit"` APIRequestContext. Outgoing Cookie, Authorization and
`X-Plugin-Capability-Token` headers are asserted with awaited
`request.allHeaders()` inspections (never `request.headers()` for Cookie) on
public bootstrap/pluginpage and direct-static flows. Header values are not
logged. Public bootstrap/GET with an explicit Cookie must be denied.

## Required environment

All values below are placeholders, not defaults. Secrets belong only in the
local shell or CI secret store; do not write a `.env` file or commit them.

| Group | Required variables |
|---|---|
| Gate / identity | `PLUGIN_ACCEPTANCE_REQUIRED=1`, `PLUGIN_ACCEPTANCE_DISPOSABLE=1`, `PLUGIN_ACCEPTANCE_ENVIRONMENT_ID` |
| Exact origins | `PLUGIN_ACCEPTANCE_WEB_ORIGIN`, `PLUGIN_ACCEPTANCE_GATEWAY_ORIGIN`, `PLUGIN_ACCEPTANCE_STATIC_ORIGIN`, `PLUGIN_ACCEPTANCE_SHELL_ORIGIN`, `PLUGIN_ACCEPTANCE_FORBIDDEN_ORIGIN` |
| Timing | `PLUGIN_ACCEPTANCE_EXPIRY_MAX_WAIT_MS` (30000–300000) |
| Real auth | `PLUGIN_ACCEPTANCE_MEMBER_USERNAME`, `PLUGIN_ACCEPTANCE_MEMBER_PASSWORD`, `PLUGIN_ACCEPTANCE_ADMIN_USERNAME`, `PLUGIN_ACCEPTANCE_ADMIN_PASSWORD` |
| Private publication | `PLUGIN_ACCEPTANCE_PLUGIN_ID`, `PLUGIN_ACCEPTANCE_PLUGIN_VERSION`, `PLUGIN_ACCEPTANCE_ARTIFACT_DIGEST`, `PLUGIN_ACCEPTANCE_FRONTEND_ENTRY`, `PLUGIN_ACCEPTANCE_FRONTEND_BASE_PATH` |
| Private grant/scope | `PLUGIN_ACCEPTANCE_GRANT_ID`, `PLUGIN_ACCEPTANCE_GRANT_EXPIRES_AT`, `PLUGIN_ACCEPTANCE_PLUGIN_INSTANCE_ID`, `PLUGIN_ACCEPTANCE_COMPANY_ID`, `PLUGIN_ACCEPTANCE_CABINET_ID`, `PLUGIN_ACCEPTANCE_PROJECT_ID`, `PLUGIN_ACCEPTANCE_PRIVATE_ROUTE_PATH` |
| Private operations | `PLUGIN_ACCEPTANCE_CAPABILITY_ID`, `PLUGIN_ACCEPTANCE_CAPABILITY_VERSION`, `PLUGIN_ACCEPTANCE_LIST_OPERATION`, `PLUGIN_ACCEPTANCE_GREET_OPERATION`, `PLUGIN_ACCEPTANCE_LIST_BACKEND_PATH`, `PLUGIN_ACCEPTANCE_GREET_BACKEND_PATH` |
| Expiring fixture | `PLUGIN_ACCEPTANCE_EXPIRING_COMPANY_ID`, `PLUGIN_ACCEPTANCE_EXPIRING_CABINET_ID`, `PLUGIN_ACCEPTANCE_EXPIRING_PROJECT_ID`, `PLUGIN_ACCEPTANCE_EXPIRING_GRANT_ID`, `PLUGIN_ACCEPTANCE_EXPIRING_GRANT_EXPIRES_AT`, `PLUGIN_ACCEPTANCE_EXPIRING_PLUGIN_INSTANCE_ID` |
| Revoke fixture | `PLUGIN_ACCEPTANCE_REVOKE_COMPANY_ID`, `PLUGIN_ACCEPTANCE_REVOKE_CABINET_ID`, `PLUGIN_ACCEPTANCE_REVOKE_PROJECT_ID`, `PLUGIN_ACCEPTANCE_REVOKE_GRANT_ID`, `PLUGIN_ACCEPTANCE_REVOKE_PLUGIN_INSTANCE_ID` |
| Negative tenant | `PLUGIN_ACCEPTANCE_WRONG_COMPANY_ID` |
| Public publication/grant | `PLUGIN_ACCEPTANCE_PUBLIC_COMPANY_ID`, `PLUGIN_ACCEPTANCE_PUBLIC_PLUGIN_ID`, `PLUGIN_ACCEPTANCE_PUBLIC_PLUGIN_VERSION`, `PLUGIN_ACCEPTANCE_PUBLIC_ARTIFACT_DIGEST`, `PLUGIN_ACCEPTANCE_PUBLIC_PLUGIN_INSTANCE_ID`, `PLUGIN_ACCEPTANCE_PUBLIC_GRANT_ID`, `PLUGIN_ACCEPTANCE_PUBLIC_GRANT_EXPIRES_AT` |
| Public frontend/operations | `PLUGIN_ACCEPTANCE_PUBLIC_ROUTE_PATH`, `PLUGIN_ACCEPTANCE_PUBLIC_FRONTEND_ENTRY`, `PLUGIN_ACCEPTANCE_PUBLIC_FRONTEND_BASE_PATH`, `PLUGIN_ACCEPTANCE_PUBLIC_CAPABILITY_ID`, `PLUGIN_ACCEPTANCE_PUBLIC_CAPABILITY_VERSION`, `PLUGIN_ACCEPTANCE_PUBLIC_OPERATION`, `PLUGIN_ACCEPTANCE_PUBLIC_GET_PATH`, `PLUGIN_ACCEPTANCE_PUBLIC_POST_PATH`, `PLUGIN_ACCEPTANCE_PUBLIC_PRIVATE_GET_PATH` |

`PLUGIN_ACCEPTANCE_HTTP_TIMEOUT_MS` is optional (1000–60000, default 30000); it
is a timeout, not an endpoint or credential fallback.

For the unmodified stock hello publication, the env values must still be set
explicitly: capability `hello.greetings@1.0`, operations `list`/`greet`, GET
`/api/greetings/list` and POST `/api/greetings`. These source contract values
are documented here, not implemented as env defaults.

## Exact invocation (only after the verification freeze is lifted)

From the repository root, in PowerShell:

```powershell
Set-Location apps/web

$env:PLUGIN_ACCEPTANCE_REQUIRED = "1"
$env:PLUGIN_ACCEPTANCE_DISPOSABLE = "1"
$env:PLUGIN_ACCEPTANCE_ENVIRONMENT_ID = "<unique-disposable-run-id>"
$env:PLUGIN_ACCEPTANCE_WEB_ORIGIN = "<https://exact-disposable-web>"
$env:PLUGIN_ACCEPTANCE_GATEWAY_ORIGIN = "<https://exact-disposable-gateway>"
$env:PLUGIN_ACCEPTANCE_STATIC_ORIGIN = "<https://exact-disposable-static>"
$env:PLUGIN_ACCEPTANCE_SHELL_ORIGIN = "<same-exact-origin-as-web>"
$env:PLUGIN_ACCEPTANCE_FORBIDDEN_ORIGIN = "<https://distinct-unused-negative-origin>"
$env:PLUGIN_ACCEPTANCE_EXPIRY_MAX_WAIT_MS = "120000"
$env:PLUGIN_ACCEPTANCE_HTTP_TIMEOUT_MS = "30000"

$env:PLUGIN_ACCEPTANCE_MEMBER_USERNAME = "<disposable-member>"
$env:PLUGIN_ACCEPTANCE_MEMBER_PASSWORD = "<secret-from-local-or-CI-store>"
$env:PLUGIN_ACCEPTANCE_ADMIN_USERNAME = "<disposable-platform-admin>"
$env:PLUGIN_ACCEPTANCE_ADMIN_PASSWORD = "<secret-from-local-or-CI-store>"

$env:PLUGIN_ACCEPTANCE_PLUGIN_ID = "<private-plugin-id>"
$env:PLUGIN_ACCEPTANCE_PLUGIN_VERSION = "<signed-semver>"
$env:PLUGIN_ACCEPTANCE_ARTIFACT_DIGEST = "<sha256:fresh-disposable-bundle-digest>"
$env:PLUGIN_ACCEPTANCE_FRONTEND_ENTRY = "<published-index.html>"
$env:PLUGIN_ACCEPTANCE_FRONTEND_BASE_PATH = "</signed/bundle/path>"
$env:PLUGIN_ACCEPTANCE_GRANT_ID = "<primary-grant-id>"
$env:PLUGIN_ACCEPTANCE_GRANT_EXPIRES_AT = "<primary-grant-expiry-ISO-8601>"
$env:PLUGIN_ACCEPTANCE_PLUGIN_INSTANCE_ID = "<primary-durable-instance-id>"
$env:PLUGIN_ACCEPTANCE_COMPANY_ID = "<primary-company-id>"
$env:PLUGIN_ACCEPTANCE_CABINET_ID = "<verified-cabinet-id>"
$env:PLUGIN_ACCEPTANCE_PROJECT_ID = "<verified-project-id>"
$env:PLUGIN_ACCEPTANCE_PRIVATE_ROUTE_PATH = "</>"
$env:PLUGIN_ACCEPTANCE_CAPABILITY_ID = "<hello.greetings>"
$env:PLUGIN_ACCEPTANCE_CAPABILITY_VERSION = "<1.0>"
$env:PLUGIN_ACCEPTANCE_LIST_OPERATION = "<list>"
$env:PLUGIN_ACCEPTANCE_GREET_OPERATION = "<greet>"
$env:PLUGIN_ACCEPTANCE_LIST_BACKEND_PATH = "</api/greetings/list>"
$env:PLUGIN_ACCEPTANCE_GREET_BACKEND_PATH = "</api/greetings>"

$env:PLUGIN_ACCEPTANCE_EXPIRING_COMPANY_ID = "<short-grant-company-id>"
$env:PLUGIN_ACCEPTANCE_EXPIRING_CABINET_ID = "<short-grant-cabinet-id>"
$env:PLUGIN_ACCEPTANCE_EXPIRING_PROJECT_ID = "<short-grant-project-id>"
$env:PLUGIN_ACCEPTANCE_EXPIRING_GRANT_ID = "<short-grant-id>"
$env:PLUGIN_ACCEPTANCE_EXPIRING_GRANT_EXPIRES_AT = "<near-future-expiry-ISO-8601>"
$env:PLUGIN_ACCEPTANCE_EXPIRING_PLUGIN_INSTANCE_ID = "<short-grant-instance-id>"

$env:PLUGIN_ACCEPTANCE_REVOKE_COMPANY_ID = "<dedicated-revoke-company-id>"
$env:PLUGIN_ACCEPTANCE_REVOKE_CABINET_ID = "<dedicated-revoke-cabinet-id>"
$env:PLUGIN_ACCEPTANCE_REVOKE_PROJECT_ID = "<dedicated-revoke-project-id>"
$env:PLUGIN_ACCEPTANCE_REVOKE_GRANT_ID = "<dedicated-revoke-grant-id>"
$env:PLUGIN_ACCEPTANCE_REVOKE_PLUGIN_INSTANCE_ID = "<dedicated-revoke-instance-id>"
$env:PLUGIN_ACCEPTANCE_WRONG_COMPANY_ID = "<company-without-member-or-installation>"

$env:PLUGIN_ACCEPTANCE_PUBLIC_COMPANY_ID = "<public-company-id>"
$env:PLUGIN_ACCEPTANCE_PUBLIC_PLUGIN_ID = "<separate-public-plugin-id>"
$env:PLUGIN_ACCEPTANCE_PUBLIC_PLUGIN_VERSION = "<public-signed-semver>"
$env:PLUGIN_ACCEPTANCE_PUBLIC_ARTIFACT_DIGEST = "<sha256:public-disposable-bundle-digest>"
$env:PLUGIN_ACCEPTANCE_PUBLIC_PLUGIN_INSTANCE_ID = "<public-durable-instance-id>"
$env:PLUGIN_ACCEPTANCE_PUBLIC_GRANT_ID = "<public-grant-id>"
$env:PLUGIN_ACCEPTANCE_PUBLIC_GRANT_EXPIRES_AT = "<public-grant-expiry-ISO-8601>"
$env:PLUGIN_ACCEPTANCE_PUBLIC_ROUTE_PATH = "</>"
$env:PLUGIN_ACCEPTANCE_PUBLIC_FRONTEND_ENTRY = "<public-index.html>"
$env:PLUGIN_ACCEPTANCE_PUBLIC_FRONTEND_BASE_PATH = "</public/signed/bundle/path>"
$env:PLUGIN_ACCEPTANCE_PUBLIC_CAPABILITY_ID = "<public.read>"
$env:PLUGIN_ACCEPTANCE_PUBLIC_CAPABILITY_VERSION = "<1.0>"
$env:PLUGIN_ACCEPTANCE_PUBLIC_OPERATION = "<read>"
$env:PLUGIN_ACCEPTANCE_PUBLIC_GET_PATH = "</api/public/read>"
$env:PLUGIN_ACCEPTANCE_PUBLIC_POST_PATH = $env:PLUGIN_ACCEPTANCE_PUBLIC_GET_PATH
$env:PLUGIN_ACCEPTANCE_PUBLIC_PRIVATE_GET_PATH = "</api/private/read>"

# One-time browser installation; performs a download, so it was NOT run here.
npm run test:e2e:install

# Real acceptance, not --list and not a mocked smoke test.
npx playwright test --config=e2e/plugin_acceptance/playwright.config.ts --project=plugin-acceptance-chromium
```

To cover both enabled HTTP entrypoints, provision fresh consumed fixtures and
run the same command separately for each explicit `GATEWAY_ORIGIN`. Do not treat
one entrypoint's result as evidence for the other. Preserve the Playwright
output showing the reporter's `collected>0` and `skipped=0` line. Traces,
videos and screenshots are disabled so Authorization headers, session bodies and
tenant data are not persisted by this harness; do not attach unredacted logs.

The `.acceptance.ts` suffix and dedicated config keep this mandatory layer out
of the repository's normal `*.spec.ts` smoke/live invocations. There is no
`test.skip`, conditional empty suite, dev/prod fallback or automatic web server.

## Coverage written, not executed

- Exact admin/bootstrap identity, grant, durable instance, publication and scope.
- Real private Chromium `host.ready → plugin.ready → grant.ready`, exact current
  iframe source/origin/nonce/channel instance/plugin/version/artifact, and
  list/greet with verified company/cabinet/project assertions. Exactly one
  plugin.ready and grant.ready is required for each host.ready.
- Host-side and plugin-side forged host.ready/grant.ready with wrong
  origin/source/nonce/digest plus wrong instance/plugin/version/company denied
  before broker HTTP transport; duplicate grant.ready and stale nonces denied.
- Anonymous private redirect/deny and member success. Anonymous public
  browser/bootstrap and exact public GET/HEAD 200 with credentials omitted and
  no Cookie/Authorization/capability token; explicit Cookie, POST on that same
  operation and non-public routes denied.
- Direct static GET/HEAD security headers and exact bundle digest.
- Reload, retry, dispose and second-load create fresh instance+nonce. Listener
  add/remove counters prove channel cleanup and detached Window evaluation must
  fail; uniqueness alone is not accepted as cleanup evidence.
- Natural expiry uses a different unrevoked fixture: first `Date.now()` must be
  at least `GRANT_EXPIRES_AT`, then a stale RPC gets no broker access, followed
  by removal, `PLUGIN_GATEWAY_GRANT_INACTIVE`, bootstrap 404 and fresh-login
  denial. `revokeCalls` must not contain that company; its stored source is only
  a stale-authority probe, not an open-channel or alert-based proof.
- The independent revoke-dedicated fixture is revoked while its current iframe
  remains open. Exact open-channel RPC receives `CAPABILITY_DENIED` with matching
  request id/nonce/instance/digest and Gateway `PLUGIN_GATEWAY_GRANT_INACTIVE`
  without backend forward, followed by reload and fresh-login denial.

**Intentionally not performed:** harness execution, `--list`, browser install,
frontend/backend tests, build, lint, formatting, compile/typecheck, network
calls, Docker, git status/diff/log checks, commit/push, live/dev/prod/shared
access, production CI and production publication verification. The plugin
platform's existing STOP-SHIP/emergency-disabled status and the protected CI
publication evidence gap remain unchanged.
