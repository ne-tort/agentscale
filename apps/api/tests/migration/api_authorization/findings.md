# MIG-AUTH-AUDIT-01 — findings

Static audit of `prodavan.main.app` (`create_app()`): every HTTP route was
checked for (a) an authentication dependency and (b) a tenant decision made on
the server rather than read from the request.

**Scope of the scan:** 452 operations (`METHOD /path`) across 40 handler modules
— the versioned `/api/v1/**` surface, the bare `/health*` probe surface, and the
framework's own `/docs`, `/redoc`, `/openapi.json`.

**Summary**

| | count |
|---|---|
| operations audited | 452 |
| anonymous, allow-listed with a justification | 20 |
| authenticated by a principal dependency | 432 |
| …of those, platform-admin only (`require_platform_admin`) | 118 |
| routes that read a tenant selector from query/body/header | 27 |
| …selector reaches a guard | 7 |
| …selector reaches no guard (**all recorded below**) | 20 |
| tenant proven indirectly (registered in `REVIEWED_INDIRECT_TENANT_PROOFS`) | 1 |

**No cross-tenant hole was found in the user-facing surface.** Every route a
tenant employee can reach that accepts a `company_id` / `owner_company_id` /
`X-Cabinet-Id` / `X-Project-Id` either calls a guard reachable from the handler
(`require_company_actor`, `require_access`, `resolve_actor_scope`,
`resolve_tenant_context`, `_project_visible`, …) or receives an already-resolved
tenant through `ResourceTenantDep`. All 20 selector routes without a guard are
behind `require_platform_admin`; the findings below are of two lesser kinds —
defence in depth on that admin surface, and one error-detail leak.

Nothing here was fixed: `apps/api/src/prodavan/api/**` is read-only for this
task, and the routes are owned elsewhere. Each finding names the owner's next
step.

---

## F-01 — platform-admin routes trust a body/query `company_id` with no second check

**Severity: informational (defence in depth).**
**Not a tenant-boundary defect:** the caller already holds
`platform.admin`, which by definition spans every tenant.

**What is not checked:** the tenant selector is used verbatim. Nothing verifies
that the company exists, is not soft-deleted, or that the operation the handler
performs belongs to it. For a platform admin that is the intended contract; the
finding is recorded because the same smell on a non-admin route would be a leak,
and because these handlers do not distinguish the two cases in code.

Affected (all `require_platform_admin`):

| method | path | selector |
|---|---|---|
| POST | `/api/v1/admin/ai-keys` | `body.company_ids` |
| POST | `/api/v1/admin/ai-models` | `body.owner_company_id` |
| POST | `/api/v1/admin/cabinets` | `body.company_id` |
| POST | `/api/v1/admin/cabinets` | `body.company_ids` |
| PATCH | `/api/v1/admin/cabinets/{cabinet_id}` | `body.company_id` |
| PATCH | `/api/v1/admin/cabinets/{cabinet_id}` | `body.company_ids` |
| PATCH | `/api/v1/admin/modules/{module_id}` | `body.company_ids` |
| GET | `/api/v1/admin/platform-events` | `company_id` |
| POST | `/api/v1/admin/search-index/reconcile-equipment` | `company_id` |
| GET | `/api/v1/admin/plugins/` | `company_id` |
| POST | `/api/v1/admin/plugins/grant` | `body.company_id` |
| POST | `/api/v1/admin/plugins/revoke` | `body.company_id` |
| POST | `/api/v1/admin/plugins/uninstall` | `body.company_id` |
| POST | `/api/v1/admin/plugins/wake` | `body.company_id` |
| PUT | `/api/v1/admin/plugins/enabled-modules` | `body.company_id` |
| POST | `/api/v1/admin/plugins/publication-approvals` | `body.company_id` |

**Suggested direction:** none required for security. If the surface is ever
opened to a "company-scoped operator" role, these handlers are the ones that
must switch to `require_company_actor` first — `api/v1/company_*` already
does exactly that (`require_company_actor` per handler), and the asymmetry is
worth a comment at minimum.

---

## F-02 — admin document-store / search-index take `company_id` from the body as a free-form scope

**Severity: informational (defence in depth), same class as F-01.**

The `document-store` and `search-index` admin endpoints take `company_id` inside
their request body and pass it straight to the storage layer as a namespace /
index scope. There is no membership check and no existence check — the only
guard is `require_platform_admin`. This matters slightly more than F-01 because
the value becomes part of a *storage key* (an index name / collection), so a
typo or a hostile admin client can address an arbitrary namespace, and the
routes are the most script-friendly surface in the API (six POSTs with a JSON
body, no path parameters).

Affected (all `require_platform_admin`):

| method | path | selector |
|---|---|---|
| POST | `/api/v1/admin/document-store/upsert` | `body.company_id` |
| POST | `/api/v1/admin/document-store/get` | `body.company_id` |
| POST | `/api/v1/admin/document-store/find` | `body.company_id` |
| POST | `/api/v1/admin/document-store/delete` | `body.company_id` |
| POST | `/api/v1/admin/document-store/ensure-indexes` | `body.company_id` |
| POST | `/api/v1/admin/search-index/indexes/ensure` | `body.company_id` |
| POST | `/api/v1/admin/search-index/indexes/delete` | `body.company_id` |
| POST | `/api/v1/admin/search-index/documents/index` | `body.company_id` |
| POST | `/api/v1/admin/search-index/documents/bulk` | `body.company_id` |
| POST | `/api/v1/admin/search-index/documents/get` | `body.company_id` |
| POST | `/api/v1/admin/search-index/documents/delete` | `body.company_id` |
| POST | `/api/v1/admin/search-index/search` | `body.company_id` |

**Suggested direction:** validate that `company_id` names a live company before
the value reaches the index/collection name (`CompanyRow` lookup, 404 on miss).
Cheap, and it turns a silent no-op write into a diagnosable error.

---

### F-05 — a guard exists on the call path but is skipped by name
**Severity: low (admin-only, and the bypass is deliberate).**

`PUT /api/v1/admin/ai-keys/{key_id}/companies`

This one is here because it is the case a static audit is most likely to get
*wrong in the safe-looking direction*: the guard search finds `require_access`
on the call path and would report the route as protected.

| method | path | selector |
|---|---|---|
| PUT | `/api/v1/admin/ai-keys/{key_id}/companies` | `body.company_ids` |

It is not protected. The path is:

```
AiKeysService.set_companies(key_id, body.company_ids, principal=admin)
  -> cascade_company_key_revoked(company_id, key_id, principal=actor)
     -> ProjectCommand.pause(..., skip_access=True)      # <- guard skipped here
```

`skip_access=True` is correct *for that caller*: the cascade is a system action
on projects the key was just detached from, and the actor is the platform admin
who caused it. The point of recording it is narrower — `set_companies` itself
never checks that `body.company_ids` names companies the caller may bind, and
the only validation on each id is existence (`_replace_bindings` raises a 422
for an unknown company), which is not authorization.

**Suggested direction:** none required while the route stays platform-admin.
If it ever accepts a company-scoped operator, the binding set must be checked
against `EntitlementService.require_company_actor` per company id *before*
`_replace_bindings`, and this entry becomes F-01-shaped at a higher severity.
`registry.REACHABILITY_CAVEATS` carries the same note in code, and a dedicated
test keeps it from going stale.

---

## F-03 — `/api/v1/stub` is public and unmounted from any auth narrative
**Severity: low.**

`GET /api/v1/stub` returns a fixed marker (`status: stub`) with no tenant read.
It is allow-listed, so it is not a leak. It is recorded because it is dead
product surface still reachable in production: a deployment that was never
implemented answers `200` here, and any smoke test that treats this as "API is
up" is measuring the wrong thing.

**Suggested direction:** delete `api/v1/stub.py` and its registration in
`api/v1/router.py` once nothing calls it, or gate it behind a settings flag.

---

## F-04 — `/api/v1/auth/*` returns a service error detail to anonymous callers

**Severity: low (information disclosure, no tenant data).**

`GET /api/v1/auth/health` is public by design (it reports whether the process can
reach the identity provider) and it returns `{"detail": exc.detail or exc.title}`
when the identity provider call raises `AppError`. `GET /api/v1/health/ready`
has the same shape for infrastructure failures (`f"database: {str(exc)[:200]}"`).
Both are unauthenticated.

The exposure is bounded — `AppError` details are written for clients, and the
readiness body is already truncated to 200 characters — but for an anonymous
route the string can still carry an upstream host name or a driver message.

**Suggested direction:** return a stable code (`auth_provider_unavailable`) and
log the exception with its trace id, instead of echoing it. The trace id is
already on every response (`X-Trace-Id`), so an operator loses nothing.

---

## Reviewed and deliberately not reported

Recorded so a reader does not mistake silence for "not looked at".

* **`POST /api/v1/projects/{project_id}/webhooks/{http,telegram}`** — public by
  design, authenticated per request by HMAC-SHA256 over the raw body against the
  routed project's company secret (`domain/projects/webhook_hmac.py`, verified in
  `application/projects/signed_ingress.py` before the payload is queued). A
  missing project and a missing secret both answer the same generic 404
  (audit API-P2d), so the route cannot be used to enumerate project ids. The
  secret *is* the tenant boundary.
* **`POST /api/v1/web/projects/{project_id}/chat/sessions/{session_id}/stream`** —
  the handler never names a tenant guard because the project/cabinet proof
  happens inside the injected runtime:
  `runtime.sessions.resolve` → `SqlChatSessionResolver.resolve` →
  `ProjectAccessPolicy.require_access`, before the session row is read
  (`application/web_facade_adapter/sessions.py`). Registered in
  `REVIEWED_INDIRECT_TENANT_PROOFS`; if that resolver is ever replaced, the
  registry entry stops being needed and the contract test fails.
* **`X-Cabinet-Id` / `X-Project-Id` headers on `/content/**`** — attacker
  controllable in principle, but `resolve_actor_scope`
  (`application/content/scope.py`) drops any cabinet/project the employee is not
  attached to before it enters `ContentActorScope`. Every `/content/**` handler
  starts with that call.
* **`/api/v1/companies/{company_id}/resources/**`** — the tenant is a
  *dependency* (`resource_tenant` → `require_company_actor`), not a body field,
  so nothing is read from the caller before the membership check.
* **`/api/v1/external/**`** — the machine surface authenticates with an
  `ak_` app key (`_external_context`), which resolves to a bound employee and a
  company; project/cabinet access then goes through `_project_visible` /
  `_cabinet_visible`, which compare against the key's own company and cabinet
  scope.
* **`/api/v1/internal/pods/**` and `/api/v1/projects/{project_id}/modules/**`** —
  Pod Identity Bridge JWTs, bound to one project/pod by
  `bridge.require_project` / `require_bridge_pod` plus a per-surface scope
  (`require_scope`).

## How to re-run

```powershell
cd C:\Users\qwerty\git\agentscale\wt-migration\apps\api
$env:PYTHONPATH="C:\Users\qwerty\git\agentscale\wt-migration\apps\api\src"
python -m pytest tests/migration/api_authorization -q --no-header -p no:cacheprovider
```

The test fails when a route appears outside `registry.PUBLIC_ROUTES` without an
authentication dependency, when a public entry loses its justification, and when
a tenant selector appears on a non-admin route with no reachable guard. A new
gap is a *test failure* until it is either fixed or recorded here and in
`registry.UNVALIDATED_TENANT_SELECTORS`.
