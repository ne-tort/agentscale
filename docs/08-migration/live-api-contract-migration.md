# Live API contract migration

Статус: migration artifact, только документация. Дата baseline: 2026-11-01.

Этот документ формализует перенос фактического API-контракта между текущим FastAPI + Flutter клиентом и целевым typed resource/domain contract. Он не меняет production API, не добавляет compatibility routes и не запускает генераторы. Любой target endpoint ниже является целевым именем/типом для будущего контракта, а не требованием немедленно переименовать live route.

## 1. Scope и источники истины

### В scope

- текущая регистрация FastAPI routers под `/api/v1`;
- фактические вызовы Flutter из `apps/flutter/lib/core/api/*.dart`;
- employee, company, platform-admin, auth, module runtime, agent/chat, container/workspace и Pod bridge surfaces;
- mapping каждого surface к typed resource/domain API с сохранением ownership и action semantics;
- порядок миграции: inventory → typed schemas → adapters → feature cutover → legacy retirement.

### Не в scope

- изменение `apps/api/**` или `apps/flutter/**`;
- изменение URL, response shape, auth policy или production deployment;
- OpenAPI/codegen запуск, тесты, build, lint и runtime verification;
- перенос внутренних `/internal/*` и Pod API в публичный клиентский contract.

### Contract authority

1. Live behavior: FastAPI routers и Pydantic response/request models в `apps/api/src/prodavan/api/`.
2. Client usage: ручные Dart clients в `apps/flutter/lib/core/api/` и feature controllers.
3. Product semantics: `docs/PRODUCT.md`.
4. Intended conventions: `docs/02-architecture/api-style.md`, но только после сверки с live code.
5. `apps/api/openapi/openapi.yaml` сейчас skeletal/stub и не является полной картой live API.

## 2. Runtime topology and boundary

| Boundary | Current source | Client owner | Target typed package/domain |
|---|---|---|---|
| Public HTTP | `apps/api/src/prodavan/api/v1/router.py` | `ProdavanApi`, `CompanyApi`, `AdminApi` | `packages/api-contracts` (planned), split by domain |
| Authentication | `api/v1/auth.py`, `identity.py` | `core/auth/*`, auth API client | `auth` resource + session policy |
| Employee work context | `api/v1/cabinets.py`, `projects.py`, `company_modules.py` | `ProdavanApi`, employee feature controllers | `cabinet`, `project`, `module-runtime` |
| Agent transport | `api/v1/agent.py` | `ProdavanApi`, chat controllers | `agent-session`, `chat-transcript`, typed SSE |
| Company administration | `company_*`, `content.py` | `CompanyApi` | `company`, `employee`, `company-cabinet`, `company-module` |
| Platform administration | `admin_*` | `AdminApi`, admin feature pages | `admin-*` resources/actions |
| Pod/bridge | `main_pod.py`, `api/internal/pods.py`, `tenant_infra.py`, `pod_modules.py` | no direct Flutter ownership | private `pod-runtime` and `tenant-infra` contracts |

All public paths are currently mounted under the configured `/api/v1` prefix. Target typed clients must receive an immutable request context (`access token`, optional `cabinetId`, optional `projectId`, optional `sessionId`) per call; mutable singleton headers in the current client are migration debt.

## 3. Current router inventory

The following is the live registration inventory from `apps/api/src/prodavan/api/v1/router.py`:

| Router module | Current route family | Target domain |
|---|---|---|
| `health`, `stub` | `/health/*`, `/stub` | `platform-health` (server-only except health probe) |
| `auth`, `identity` | `/auth/*`, `/me`, identity links | `auth`, `identity` |
| `cabinets` | `/cabinets/*` | `cabinet` |
| `projects` | `/projects/*`, `/cabinets/{cabinet_id}/projects/*` | `project`, `project-lifecycle` |
| `agent` | `/projects/{project_id}/agent/*`, `/chat*`, sessions, events | `agent-session`, `chat` |
| `company_ai_keys`, `company_ai_models`, `company_containers`, `company_modules` | `/companies/{company_id}/...` | `company-ai`, `company-runtime`, `company-module` |
| `ai_keys` | employee/project AI key availability and scoped key routes | `ai-credentials` |
| `content`, `documents` | content assets and project documents | `content-asset`, `document` |
| `external`, `b2b` | webhooks/app keys and tender compatibility surface | `integration`, `tender-compat` |
| `tenant_infra`, `pod_modules` | project infra/modules bridge-facing routes | private `tenant-infra`, `module-runtime` |
| `admin_companies`, `admin_profile`, `admin_metrics` | platform company/profile/metrics | `admin.company`, `admin.profile`, `admin.metrics` |
| `admin_cabinets`, `admin_projects`, `admin_containers`, `admin_modules` | platform resource management | `admin.cabinet`, `admin.project`, `admin.container`, `admin.module` |
| `admin_ai_models`, `admin_catalogs`, `admin_search_index` | catalogs, models, indexes | `admin.catalog`, `admin.ai-model`, `admin.search` |
| `admin_document_store`, `admin_object_store`, `admin_platform_events` | operational storage/events | `admin.operations` |
| `admin_triggers`, `admin_recycle` | maintenance/actions and restore/purge | `admin.operations` |
| `internal_pods` | `/internal/pods/*` | private `pod-runtime`; never generated for Flutter |

The separate `apps/api/src/prodavan/main_pod.py` application registers a reduced Pod API (`agent`, `tenant_infra`, `pod_modules`, `documents`, `internal_pods`). It is a different trust boundary and must not be merged into the public client contract.

## 4. Current client areas

| Current client area | Source | Main live concerns | Target typed client boundary |
|---|---|---|---|
| Auth/session | `apps/flutter/lib/core/auth/*` | login, refresh, logout, OIDC config, token persistence | `AuthApi`, `SessionStore`, typed `AuthError` |
| Employee shell/work context | `core/session/*`, `core/api/prodavan_api.dart` | `/me`, cabinet selection, project selection, headers | `WorkContext` + `CabinetApi`/`ProjectApi` |
| Cabinet hubs and module runtime | `features/meta/*`, `ProdavanApi` | manifest, meta documents, rows, binds, actions, uploads | `ModuleManifestApi`, `ModuleDataApi`, `ModuleActionApi` |
| Projects and containers | `features/employee/*`, `features/containers/*` | CRUD, launch/pause/resume/reload/sync, metrics | `ProjectResource`, `ProjectLifecycleApi`, `ContainerReadApi` |
| Agent chat | `features/employee/*`, `core/chat/*`, `ProdavanApi` | sessions, drafts, transcript, send/cancel, approvals, sidechains | `AgentSessionApi`, `ChatTranscriptApi`, typed `ChatEventStream` |
| Workspace/files | `features/containers/*`, `ProdavanApi` | entries, preview, download, attachment upload/download | `WorkspaceApi`, `ContentAssetApi` |
| Company admin | `features/company/*`, `CompanyApi` | employees, assignments, cabinets, AI keys/models, containers | `CompanyApi` split into `EmployeeApi`, `CompanyCabinetApi`, `CompanyAiApi`, `CompanyContainerApi` |
| Platform admin | `features/admin/*`, `AdminApi` | companies, modules, catalogs, models, profile, operational actions | separate `Admin*Api` resources; no employee client dependency |
| Integration/tender compatibility | `features/employee/webhooks_page.dart` and API calls | webhooks/app keys/tenders | `IntegrationApi`, `TenderCompatibilityApi` |

The three large handwritten clients are transport/domain mixed surfaces. They should become thin typed facades; feature code should depend on domain interfaces, not on `jsonDecode` maps or mutable `cabinetId/projectId` fields.

## 5. Current → target endpoint mapping

### 5.1 Auth and identity

| Current live route(s) | Current caller | Target typed resource/domain endpoint | Migration note |
|---|---|---|---|
| `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout` | `core/auth/*` | `auth.sessions.login`, `.refresh`, `.logout` | Preserve token refresh and 401 retry semantics; typed error envelope. |
| `GET /auth/config`, `GET /auth/health`, broker start/callback | auth bootstrap | `auth.configuration`, `auth.identityBroker` | Browser/OIDC helpers remain transport-specific. |
| `GET /me` | all three API clients | `identity.currentUser` | One shared response model; avoid three decoders. |
| employee contact/password routes | `ProdavanApi`, `CompanyApi` | `identity.credentials.update`, `identity.contact.update` | Scope and authorization stay server-defined. |

### 5.2 Cabinet, project and lifecycle

| Current live route(s) | Target typed resource/domain endpoint |
|---|---|
| `GET/POST /cabinets`, `GET/PATCH/DELETE /cabinets/{id}` | `cabinets.list`, `cabinets.create`, `cabinets.get`, `cabinets.update`, `cabinets.archive` |
| `POST /cabinets/{id}/restore`, `/purge`, `/copy` | `cabinets.restore`, `cabinets.purge`, `cabinets.copy` actions |
| `GET/PUT /cabinets/{id}/me/selection` | `cabinetSelection.get`, `cabinetSelection.set` |
| `GET /cabinets/{id}/chats/sidebar` | `chatSidebar.get` (read model, not generic cabinet child) |
| `GET/POST /cabinets/{id}/projects`, `GET/PATCH/DELETE /projects/{id}` | `projects.list`, `projects.create`, `projects.get`, `projects.update`, `projects.archive` |
| `POST /projects/{id}/launch`, `/pause`, `/resume`, `/reload`, `/sync` | `projectLifecycle.launch/pause/resume/reload/sync` |
| `GET /projects/{id}/container`, `/container/metrics` | `projectRuntime.getContainer`, `.getMetrics` |
| `GET /projects/{id}/metrics`, `/cabinets/{id}/metrics*` | `metrics.projectOverview`, `metrics.cabinetOverview`, `metrics.series` |
| `GET/POST /projects/{id}/modules`, module bind/revoke routes | `projectModules.list`, `.bind`, `.revoke`, `.updateProfile` |

Typed project commands must expose command-specific request/response types and lifecycle status enums. They must not collapse `launch`, `resume`, `reload` into a generic `POST action` because idempotency, progress and error handling differ.

### 5.3 Module runtime and data

| Current live route(s) | Target typed resource/domain endpoint |
|---|---|
| `GET /cabinets/{cid}/modules`, `GET /projects/{pid}/modules` | `moduleBindings.list` / `moduleRuntime.list` |
| `GET .../meta/documents/{slug}` | `moduleMeta.getDocument` |
| `GET .../data/{table_slug}` | `moduleData.listRows` with typed pagination/filter contract |
| `POST .../data/{table_slug}` | `moduleData.createRow` |
| `PATCH .../data/{table_slug}/{row_id}` | `moduleData.patchRow` (merge semantics explicit) |
| `DELETE .../data/{table_slug}/{row_id}` | `moduleData.deleteRow` |
| `POST .../actions/{action_id}/invoke` | `moduleActions.invoke` with discriminated action result (`fileRef`, rows, status) |
| `POST .../content/upload`, `/secrets/upload` | `moduleContent.upload`, `moduleSecrets.upload` |
| `GET /admin/modules/*`, company module routes | `adminModule` / `companyModule` ownership-specific API, sharing common `ModuleMeta` and `ModuleRow` schemas |

`X-Prodavan-Session-Id` is a domain scope for `scope.chats=current`, not a generic HTTP context. The typed contract must represent it as an optional `chatScope.sessionId` and preserve the distinction between `all` and `current` rows.

### 5.4 Agent, chat and streams

| Current live route(s) | Target typed resource/domain endpoint | Transport |
|---|---|---|
| `POST/GET/PATCH/DELETE /projects/{pid}/agent/sessions*` | `agentSessions.create/list/get/update/delete` | JSON REST |
| `POST .../send`, `/cancel`, `/fork` | `agentSessions.send/cancel/fork` | JSON command REST |
| `GET .../events`, `/transcript`, `/sidechains/.../transcript` | `chatTranscript.events/transcript/sidechainTranscript` | JSON + polling |
| `GET/PUT/DELETE .../composer-draft` | `chatDrafts.get/put/delete` | JSON REST |
| `POST /projects/{pid}/chat` | `chatTurns.create` | JSON command REST |
| `POST /projects/{pid}/chat/stream` | `chatTurns.stream` | typed SSE; event union required |
| `GET /projects/{pid}/chat/transcript` | `chatTranscript.get` | JSON read model, supports `after_seq`/`before_seq` |
| `GET/POST .../pending-approvals`, `/tool-approvals` | `toolApprovals.list/resolve` | JSON REST |
| `GET/PUT /projects/{pid}/chat-error-policy` | `chatPolicy.get/update` | JSON REST |
| `POST /projects/{pid}/agent/reset` | `agentSessions.resetProject` | destructive command; explicit confirmation and result type |

The SSE contract must model at least `text_delta`, tool lifecycle, session status, usage, error and terminal events. Client rendering remains append-only; the typed stream must not perform text overlap merging. Transcript blocks and event payloads are separate types.

### 5.5 Workspace, documents and content

| Current live route(s) | Target typed resource/domain endpoint |
|---|---|
| `/projects/{pid}/workspace/*` and company/admin container workspace routes | `workspace.entries`, `workspace.preview`, `workspace.download`, `workspace.delete`, `workspace.move`, `workspace.copy` |
| `/projects/{pid}/attachments*` | `contentAssets.list/upload/delete/download` |
| `/projects/{pid}/content/*`, `/documents/*` | `documents.list/create/read/fill/convert` plus `contentAssets` |
| module/admin/company content upload routes | ownership-specific `contentAssets.upload(scope)` with a common `FileRef` result |

Downloads must be typed as binary/file responses with filename and media type metadata; they are not `Map<String,dynamic>`. Multipart upload and JSON action invocation need separate request builders.

### 5.6 Company and platform admin

| Current live route family | Target typed resource/domain endpoint |
|---|---|
| `/companies/{cid}/employees*` and employee enable/disable/password/contact | `companyEmployees.list/create/updateCredentials/updateContact/enable/disable` |
| `/companies/{cid}/cabinets*`, assignments | `companyCabinets.list/create/copy`, `cabinetAssignments.list/assign/revoke` |
| `/companies/{cid}/ai-keys*`, `/ai-models*` | `companyAiKeys.*`, `companyAiModels.*` |
| `/companies/{cid}/containers*` | `companyContainers.list/get/pause/resume/reload/workspace` |
| `/companies/{cid}/modules*` | `companyModules.list/get/updateProfile/data/content` |
| `/admin/companies*` | `adminCompanies.list/get/create/update/archive/restore/purge` |
| `/admin/profile*` | `adminProfile.get/updateLogin/updatePassword` |
| `/admin/modules*`, `/admin/catalogs*`, `/admin/ai-models*` | `adminModules.*`, `adminCatalogEntries.*`, `adminAiModels.*` |
| `/admin/metrics*` | `adminMetrics.companies/series/rebuild` |
| `/admin/search-index*`, `/admin/document-store*`, `/admin/object-store*` | `adminSearch`, `adminDocumentStore`, `adminObjectStore` operational clients |
| `/admin/triggers*`, `/admin/recycle*`, `/admin/platform-events*` | `adminOperations` commands; excluded from employee client |

Admin and company APIs may share schema primitives, but must not share authorization context implicitly. A typed endpoint function declares its owner scope (`platform`, `company`, `cabinet`, `project`) and required path identifiers.

### 5.7 Private and compatibility surfaces

| Current route family | Target treatment |
|---|---|
| `/internal/pods/*` | private `PodRuntimeClient`; no public generated client, no Flutter mapping |
| `/projects/{pid}/infra/*` | private `TenantInfraGatewayClient`; typed for Pod bridge only |
| `/projects/{pid}/modules/*` Pod module endpoints | private `ModuleBridgeClient`; share schemas only where wire-compatible |
| `/b2b/*`, `/external/*` | isolate under `compat.integrations`; migrate only when product owner defines a stable domain contract |

## 6. Shared typed primitives

The future contract package should define these once and reuse them across domains:

- `ResourceId` / UUID and `CursorPage<T>` or explicit page response types;
- `ProblemDetails` with `code`, `traceId`, `fieldErrors` and status;
- `RequestContext` (`accessToken`, `cabinetId`, `projectId`, `sessionId`, `requestId`);
- `CabinetScope`, `CompanyScope`, `ProjectScope`, `ChatScope` as distinct types;
- lifecycle enums (`draft`, `active`, `paused`, `error`, `completed`) and observed pod states;
- `ModuleManifest`, `ModuleMetaDocument`, `ModuleRow`, `FileRef`, `ActionResult` discriminated unions;
- `AgentSession`, `TranscriptBlock`, `ChatEvent` discriminated union and `UsageSummary`;
- `Page<T>` with the actual live pagination variant preserved (page/page_size for virtual module tables versus cursor/sequence for transcript);
- `BinaryDownload` (`bytes`, `filename`, `contentType`) and `MultipartUploadResult`.

Typed models must preserve nullable versus omitted PATCH fields. Module row PATCH is merge semantics; it is not a full replacement. `204` responses must be represented as `void`/unit, not decoded as JSON.

## 7. Migration sequence

1. **Freeze inventory:** keep this document updated whenever a live route or Flutter call is added; record source file and owner scope.
2. **Extract schemas:** create a versioned typed contract from live FastAPI models/routes, explicitly adding SSE, multipart and binary operations absent from the current OpenAPI stub.
3. **Build adapters:** introduce typed domain clients behind the existing `ProdavanApi`, `CompanyApi` and `AdminApi`; no route changes.
4. **Migrate low-risk reads:** health, identity, cabinet/project reads, metrics and module manifests.
5. **Migrate writes and commands:** lifecycle, module rows/actions, company/admin mutations; preserve status and idempotency behavior.
6. **Migrate chat separately:** transcript/block projection first, then typed SSE, then send/cancel/approvals and sidechains.
7. **Retire dynamic maps:** feature controllers consume typed models; remaining `Map<String,dynamic>` usage is limited to declared extension/meta JSON payloads.
8. **Compatibility review:** compare request paths, headers, query parameters, status codes, error codes and response fields before each feature cutover.
9. **Only after parity:** decide whether legacy clients/routes can be deprecated. This artifact does not authorize removal.

## 8. Acceptance checklist for the eventual contract

- Every registered public router has an owner domain and typed client disposition.
- Every Flutter API method maps to exactly one typed operation or is explicitly marked compatibility/private.
- Every typed operation records method, path, required headers, query/body schema, success status and error model.
- SSE event names and payloads are a discriminated union; reconnect/sequence behavior is documented.
- Multipart uploads and binary downloads have non-JSON response types.
- Auth, cabinet, project and session scope cannot be silently inherited from mutable global client state.
- Module `all` versus `current` chat scope and merge PATCH semantics are represented in types.
- Admin/company/private Pod contracts are separated by authorization boundary.
- Generated artifacts, if adopted later, are reproducible from a pinned source and checked for drift against live routes.
- No production endpoint is renamed or removed until a separately approved compatibility/cutover plan exists.

## 9. Known gaps and explicit follow-ups

- `apps/api/openapi/openapi.yaml` is a contract stub and cannot currently drive a complete client migration.
- `ProdavanApi` is approximately 1,800 lines and contains transport, context mutation, decoding and domain behavior in one class.
- `CompanyApi` and `AdminApi` duplicate `/me`, cabinet, employee and credential methods with different ownership assumptions.
- Flutter uses dynamic JSON maps for module metadata/data by design; typed migration should retain an explicit JSON extension boundary rather than pretending all module DSL is static.
- The current API style document describes intended `/v1` envelopes and headers that must be reconciled against live responses before they are made normative.
- A future React/TypeScript client is not assumed here: the target contract is language-neutral and can first be consumed by Flutter adapters or a later TypeScript client.
