# Source API contract inventory

Статический migration reference между текущим FastAPI API и React web client. Baseline: 2026-11-02. Это продолжение [`../live-api-contract-migration.md`](../live-api-contract-migration.md), не второй независимый inventory. В старом документе остаются typed-target mapping и sequence; этот каталог является SoT именно для live route coverage и текущих UI mismatches.

## Sources of truth

1. `apps/api/src/prodavan/main.py`, `config/settings.py` (`api_v1_prefix = "/api/v1"`), and `api/v1/router.py` define the public app and mount.
2. Route decorators, handler bodies, request models, dependencies, serializers, and `exception_handlers.py` define wire behavior.
3. React request sources are `apps/web/src/api/client.ts`, `apps/web/src/api/types.ts`, and feature adapters under `apps/web/src/features/**`.
4. `apps/api/openapi/openapi.yaml` is explicitly a draft/stub, not a complete source.
5. [`generate_routes.py`](generate_routes.py) extracts registered `include_router` instances and route decorators into the adjacent catalog. From the repository root run `python docs/08-migration/api-contracts/generate_routes.py`; it performs only static source parsing and writes `routes-current.md`.


## Inventory files

- [`routes-current.md`](routes-current.md): every operation reached through the public `api/v1/router.py` registration, plus the four `/internal/pods/*` operations mounted there. Rows include method, exact path, router owner, body/media, query, headers, status/response, auth boundary, and source file:line.
- [`../live-api-contract-migration.md`](../live-api-contract-migration.md): target typed contract boundaries and migration order. Its legacy broad route table has been replaced by links here and source-verified integration findings below.

## Registration and count

- Main FastAPI app includes `health.router` directly at the root and mounts `v1_router` at configured `/api/v1`.
- Main FastAPI app mounts **38 router instances** under configured `/api/v1`; route catalog records **407 `/api/v1` operations**, including four `/internal/pods/*` operations. Three health probes are additional registrations outside `/api/v1`; health handlers also mount under the prefix.
- `main_pod.py` is a separate listener/application that remounts health and a reduced set of families (`agent`, `tenant_infra`, `pod_modules`, `documents`, `internal_pods`) under `/api/v1`. It is not a browser base URL.
- Router-family counts are in the route catalog; the two compound registrations (`admin_companies.company_router` and `projects.cabinet_projects_router`) remain separate so nobody mistakes company or cabinet-scoped routes for admin-project routes.

## Wire-level rules

- JSON API methods use OIDC `Authorization: Bearer <access_token>`; platform-admin operations additionally require `platform.admin`. External app-key chat/API methods use a distinct app-key bearer plus route scopes; `/external/app-keys`, `/external/webhooks`, and `/external/webhook-deliveries` use OIDC principal plus active employee/company ownership. The external module docstring's OIDC claim for app-key methods is stale.
- Errors use RFC-7807-style `application/problem+json` with `type`, `title`, `status`, `code` and optional `detail`, `message`, `trace_id`; validation is 422 and conflict is 409. Success status defaults and list envelopes vary by route; see the inventory.
- `GET /me` alone consumes `X-Cabinet-Id` and `X-Project-Id` through `WorkContextDep`; they are not universal scope headers. Module row/action routes separately accept `X-Prodavan-Session-Id`. Durable run commands require `Idempotency-Key` and expected sequence/state fields.
- Signed project webhook ingress accepts raw bytes plus HMAC `X-Prodavan-Signature`; Pod bridge operations require scoped Pod Identity Bridge JWT. `AgentAuthDep` is route-specific OIDC or bridge authentication.
- Multipart module-content upload is `POST` `multipart/form-data`, one `file` part (`UploadFile`). The browser must send `FormData` and must not set `Content-Type` itself so the browser supplies the boundary.
- Workspace write is raw request bytes: `PUT .../workspace/content?path=...`; max 10 MiB; `src/dst` move/copy are JSON. Workspace download is raw bytes, `application/octet-stream`, `Content-Disposition: attachment; filename=...`, plus `X-Workspace-Path` and `X-Workspace-Size`; max 10 MiB.
- Content asset version upload is raw bytes `PUT /content/assets/{asset_id}/versions/{version_id}/upload`, with the file media type in `Content-Type`. Asset download is `GET .../download?blob_version_id=...&proxy=1`: without `proxy=1` it may be a 302 to object storage; with proxy it streams raw bytes using stored MIME. The handler does not set `Content-Disposition`, so the client has no guaranteed filename header.
- Project attachment upload is JSON base64 (`filename`, `content_base64`, optional `content_type`), not multipart. Attachment download is raw bytes using original media type and `Content-Disposition: inline; filename=...`.
- `POST /projects/{project_id}/chat/stream` is the live legacy SSE contract; request body is `{text,session_id?,attachment_refs,model?,turn_id?}`, with `text/event-stream` response frames shaped `{type,data,...}`. Keepalive is an SSE comment about every 15 seconds. Stream-time errors are `_error` payloads after HTTP 200; successful turns emit `_turn_complete`; retries reuse `turn_id`, with no `id:`/`Last-Event-ID` replay. Current React clients disagree on incomplete EOF: feature service requires the terminal frame, central client does not; the feature service also defaults to the incorrect `/api` prefix noted below.

## Implementable UI integration gaps

| Priority | Evidence | Finding and integration consequence |
|---|---|---|
| P1 | `apps/api/src/prodavan/api/v1/router.py:74-79`; `projects.py:347-358`; `pod_modules.py:51-61`; `main.py`, `main_pod.py` | **Registered route collision:** both routers declare `GET /api/v1/projects/{project_id}/modules`; main app includes the OIDC `projects.router` first, so a Pod Bridge JWT matches that route and is rejected by `PrincipalDep` before the bridge route. `main_pod.py` mounts only `pod_modules` and does not have the duplicate. |
| P1 | `apps/web/src/features/chat/service.ts:153,266`; `apps/web/src/api/client.ts:220-222`; FastAPI `agent.py:387` | The active chat feature defaults to `/api`, while FastAPI mounts `/api/v1`; ingress forwards `/api` unchanged and the central client uses `/api/v1`. The chat stream therefore targets a path with no registered route. Configure the chat service base URL to `/api/v1` or route it through an explicit rewriting proxy. |
| P1 | `apps/web/src/api/types.ts:15-19`; `apps/web/src/app/auth/lifecycle.ts:123-132,159-168`; `identity.py:74-112` | Web `CurrentUserResponse.active` is required and auth restore/sign-in reject it when absent. Live `GET /me` never sends top-level `active`; it returns `employee.status` nested for employee principals and `employee: null` for company principals. `normalizeCurrentUser` does not derive the client field. Valid successful `/me` responses therefore cause client-side sign-out on restore/sign-in. Map employee status where present and define company-principal behavior, or align the client contract with the live response. |
| P1 | `apps/web/src/features/runs/RunsCenterPage.tsx:297-298`; `apps/web/src/features/runs/service.ts`; `apps/api/src/prodavan/api/v1/agent_runs.py`; `migration_agent_runs/dependencies.py:103-139` | Durable Run routes are registered but not operationally ready: backend access requires `PRODAVAN_AGENT_RUN_API_ENABLED`, registered ORM models, and existing DB tables. The dependency constructs `writes_enabled=False`, so run create/cancel/approval decision return 503 at `require_writes()`. Runs UI still calls legacy session APIs and its statement that the backend has no run endpoints is stale. Reads can be integrated once readiness is configured; writes need a wired dispatcher. |
| P1 | `apps/web/src/features/automations/service.ts:150-169`; `AutomationsPage.tsx:151,377,387-393`; `external.py:768-905` | Automation adapter calls `/webhooks`, `/webhooks/{id}` without the live `/external` prefix. Live outgoing subscription and delivery endpoints are `/external/webhooks`, `/external/webhook-deliveries`, and `/external/webhook-deliveries/{id}/retry`; the adapter lacks delivery list/retry methods. It already uses employee OIDC, which is correct for these routes, and sends `project_id` on create while preserving the one-time secret. Per-trigger dispatch/result remains a distinct absent API. |
| P2 | `apps/web/src/features/contours/CompanyContour.tsx`; `AdminContour.tsx`; `contours/service.ts`; `identity.py`, `admin_companies.py`, `company_ai_keys.py`, `company_containers.py` | Company/admin UI coverage is partial. Company employee table exposes contact/password edits but not create, enable/disable, restore, or soft-delete; company key scope/OAuth/probe and company container operations are not wired. Admin contour covers selected company/quota/cabinet/module/catalog/metrics/recycle operations, not the broader policy/subscription/tenant-quota/container routes. Treat these as UI integration gaps where routes exist. |
| P2 | `CompanyContour.tsx:71-81`; `company_ai_models.py:20-47` | The model form writes only `name`, `model_ids`, `provider`, `description`; the live body also supports reasoning level, pricing, context limit, publisher, and release date. This is a deliberate partial editor. |
| P2 | `apps/web/src/features/contours/module-admin/ModuleAdminPage.tsx:124-163,286-325`; `module-admin/service.ts:106-127`; `admin_modules.py`, `company_modules.py`, `cabinets.py` | Module-admin UI reads module detail/meta/binding catalogs, edits admin grants/cabinet bindings, manages project binds, and reads cabinet projects/installations plus optional `templates` data. It does not expose broader central-client metadata/content/secret/action/data operations. Company module metadata has no DELETE route, but the UI only reads it. |
| P2 | `apps/web/src/features/chat/service.ts:288-330`; `apps/web/src/api/client.ts:276-331`; `agent.py` | Both clients parse legacy `{type,data}` SSE. The feature service requires `_turn_complete` and retries an incomplete stream with the same `turn_id`; the central API client accepts EOF without the terminal frame. This is a client behavioral mismatch, not a server schema gap. |

## Target APIs that are genuinely absent

- Per-trigger dispatch/retry/result and durable trigger-to-Run linkage. Existing project dispatch is bulk by `max`; platform drain is global; outgoing webhook retry is a separate delivery resource.
- Run cost/usage summary and run-associated artifact endpoints. Event timeline exists as a gated route but is not an artifact API or cost summary.
- Company-module metadata delete, only if parity with admin metadata authoring is a product requirement.

Durable Agent Run list/detail/cancel/approval/event routes are registered, but require feature/table readiness and writes are fail-closed until a dispatcher is wired. Do not describe these as nonexistent routes or available operations.

## Freeze

Static source inspection and documentation only. No code outside `docs/**` is changed; no API/OpenAPI generation, tests, builds, lint, formatting, or git/formal checks are run. Updating this inventory requires checking router registration and client call sites again; do not promote a route to “implemented UI” based only on a UI label or type declaration.
