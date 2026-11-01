# Frontend migration audit: Flutter -> React + TypeScript

Дата аудита: 2026-11-01

## Executive summary

- Единственный фактически реализованный frontend в checkout — `apps/flutter`. Отдельного каталога `employee-ui/` нет, хотя `docs/PRODUCT.md` ссылается на `employee-ui/README.md`; `reference-ui/` содержит только README с инструкциями для локальных, некоммитимых клонов LibreChat/Open WebUI.
- Flutter-код существенно опережает описанный в старом `apps/flutter/README.md` каркас: реализованы auth/session restore, employee cabinet shell, company и admin contours, project/container management, SSE chat/transcript, attachments, workspace files, module runtime и meta interpreters.
- Основной DSL/meta-syntax — JSONB-документы модуля: `tables`, `columns`, `views`, `tabs`, `actions`, `materialize`, `mcp_tools`, `seed_rows`, `syntax_version`, плюс `ui_json` внутри view/tab. Flutter не генерирует domain screens из кода модуля, а интерпретирует эти документы через collection/form/hub/profile-hub.
- Реальные REST-контракты UI находятся в ручных Dart API-клиентах и FastAPI routers. `apps/api/openapi/openapi.yaml` — draft/skeletal и покрывает лишь health/login/cabinets/projects; перенос нельзя строить только по нему.
- Самые ценные reusable-части для React: meta manifest model/validation boundary, generic collection/form/hub interpreters, runtime data adapter/controller contract, auth refresh policy, chat block projection/SSE protocol, scope/bind semantics, design tokens. Нельзя механически переносить Flutter widget tree или смешанные domain-specific ветки интерпретатора.

## Repository inventory

### Flutter application

Entry and boot:

- `apps/flutter/lib/app.dart:14` — `ProdavanApp`; settings/theme/localization and `SessionGatePage`.
- `apps/flutter/lib/features/auth/session_gate_page.dart:19` — restore -> validate `/me` -> post-login navigation; offline state.
- `apps/flutter/pubspec.yaml:30-56` — runtime dependencies: `http`, file/drop/paste packages, secure storage, shared preferences, Markdown; no router/state-management/codegen dependency despite older architecture docs describing go_router/Riverpod/OpenAPI codegen.

Main contours/screens by source tree:

- Employee: `apps/flutter/lib/features/employee/` — `CabinetShell`, cabinet picker/overview/projects/chats rail, project workspace, project settings/model/error policy/dialogs, module list/edit/table, management/data hubs, webhooks, tool approvals.
- Company: `apps/flutter/lib/features/company/` — company overview/settings, employees and cabinet assignments, containers, AI keys/models/OAuth probe, module list/detail/JSON/project/cabinet bindings.
- Admin: `apps/flutter/lib/features/admin/` — companies, cabinets, containers, AI keys/models/providers, metrics, modules/meta JSON, module bindings, company policy/quotas/events.
- Meta: `apps/flutter/lib/features/meta/` — module shell navigation, manifest/repository, runtime scopes/controllers, interpreters, file/secret/upload and domain widgets.
- Core chat: `apps/flutter/lib/core/chat/` — block models/projection/turn grouping, transcript cache, append-only streaming controller, Markdown/table normalization and renderers.
- Containers: `apps/flutter/lib/features/containers/` — workspace tree, preview, content/download/save paths.

The documented screen inventory in `docs/04-frontend/screens-inventory.md:18-180` is a useful conceptual M00-M09 map, but it is partly stale versus the as-built employee/company/admin/module shells. Current product behavior is more accurately described by `docs/PRODUCT.md:40-59` and the source tree.

### employee-ui and reference-ui

- `employee-ui/` is absent from the checkout (glob and content search found no directory/file). `docs/PRODUCT.md:99-100` points to it as an employee UI canon, so this link is currently broken and must be resolved before using it as migration input.
- `reference-ui/README.md:1-32` is documentation only. It instructs cloning LibreChat and Open WebUI locally and explicitly says they are not vendored or production dependencies. Reusable ideas are SSE streaming, tool cards, HITL approval, session list/fork and attachments; auth/routing/provider stacks must not be copied.

## DSL / meta-syntax map

### Canonical meta document axes

`docs/target/06-modules/meta-syntax/README.md:20-37` defines four axes:

1. Schema: `tables`, `columns`.
2. UI: `views`, `tabs` and per-view `ui_json`.
3. Runtime: `actions`, `materialize`, `mcp_tools`.
4. Scope: `enabled`, `scope`, project bindings and chat scope.

Storage/document slugs are implemented in `apps/flutter/lib/features/meta/module_meta_manifest.dart:5-27`. `ModuleMetaManifest` parses and serializes the same payload at `:30-141`, with `syntax_version` and `seed_rows` support.

### UI interpreter pipeline

- Navigation loader: `apps/flutter/lib/features/employee/cabinet_nav_loader.dart:8-57,95-163,195-238` loads module tabs, filters enabled/placement, resolves actual bind kind and chooses cabinet versus project leaf.
- Module host: `apps/flutter/lib/features/employee/cabinet_module_host.dart:20-39,98-178,190-271` fetches meta documents, creates a runtime data controller/adapter, selects a view and mounts the interpreter.
- View dispatch: `apps/flutter/lib/features/meta/interpreters/hub_interpreter.dart:82-145` maps `ui_json.kind` to `collection`, `form/detail`, `hub`, `profile_hub`; invalid kinds become an empty state.
- Collection DSL: `apps/flutter/lib/features/meta/interpreters/collection_interpreter.dart:44-122,155-195,210-300` supports columns, title field, server paging, row styles, selection, editable cells, row taps, actions, polling, summaries, document fields and equipment-specific extensions. File is ~2.3k lines: generic interpreter and product-specific behavior are currently coupled.
- Form DSL: `apps/flutter/lib/features/meta/interpreters/form_interpreter.dart:36-55,173-207,219-298` handles declarative fields, visibility/polling, defaults, row create/patch and many specialized editors.
- Hub DSL: `apps/flutter/lib/features/meta/interpreters/hub_interpreter.dart:17-79` renders navigation items and chat-scope visibility.
- Runtime scope: `apps/flutter/lib/features/meta/runtime/module_runtime_scope.dart:24-205` injects owner/project/session context plus content/secret uploads and action invocation.
- Generic data facade: `apps/flutter/lib/features/meta/runtime/runtime_data_adapter.dart:7-99` is the most direct UI-independent boundary for rows, paging, CRUD and actions.
- Data implementation: `apps/flutter/lib/features/meta/runtime/cabinet_data_controller.dart:14-29,41-59,100-193,195-255` implements session-scoped rows, cross-chat rows, OpenSearch server-paged tables, reload and project/cabinet endpoint selection.

### Non-UI semantics the React client must preserve

- Module ownership and SoT resolution: platform -> company -> cabinet -> project; local/global bind and `child_may_edit` are described in `docs/PRODUCT.md:122-170`.
- Chat scope: `all` versus `current`, `X-Prodavan-Session-Id`, active chat context (`docs/PRODUCT.md:174-185`); UI helpers include `apps/flutter/lib/features/meta/runtime/chat_scope.dart` and `WorkContext`.
- Actions are declarative metadata, not arbitrary UI callbacks; file downloads and scaffold actions are driven from meta (`apps/flutter/lib/features/meta/module_scaffold_actions.dart`, `module_action_file_download.dart`).
- Invalid meta must produce an explicit empty/error state, not silently fall back to hardcoded domain UI (`docs/target/06-modules/meta-syntax/README.md:95-101`).

## API contracts used by UI

### Actual client boundary

- `apps/flutter/lib/core/auth/auth_http.dart:5-79` centralizes Bearer attachment, one forced refresh on 401 and retry.
- `apps/flutter/lib/core/auth/token_session.dart:15-197` owns persisted access/refresh/id tokens, expiry skew, restore, password login and context synchronization.
- `apps/flutter/lib/core/api/prodavan_api.dart:8-28` owns base URL, mutable cabinet/project work headers and manual JSON decoding. It contains ~1,800 lines and calls employee project/cabinet/module/chat/container/workspace endpoints.
- `apps/flutter/lib/core/api/company_api.dart` covers `/companies/{id}` employees, assignments, cabinets, AI keys/models/probes, containers and module data/actions.
- `apps/flutter/lib/core/api/admin_api.dart:649-951` covers admin modules, metadata, data rows, uploads, secrets, actions; preceding methods cover admin companies/cabinets/keys/containers/metrics.

The API surface actually used includes:

- Auth/identity: `/auth/config`, `/auth/login`, `/auth/refresh`, `/auth/logout`, `/me`, broker start/callback.
- Employee cabinet/project: `/cabinets`, `/cabinets/{id}`, selection, sidebar, metrics, presence, `/cabinets/{id}/modules`, `/cabinets/{id}/projects`, project CRUD, assignments, lifecycle launch/sync/pause/resume/reload/complete, container/workspace and AI model selection.
- Module runtime: cabinet/project/admin/company module list; meta documents; rows CRUD; `X-Prodavan-Session-Id`; content upload; secret upload; action invocation; server-paged catalog tables.
- Chat: `/projects/{id}/chat/stream`, `/chat/transcript`, sessions CRUD/fork/cancel, drafts, pending approvals, tool approvals, sidechain transcripts, model/error policy and attachments.
- Company/admin: companies/employees/cabinet assignments, AI keys/model catalog/probes/OAuth, quotas/policy/metrics, module bindings/meta/data, admin event/trigger/container APIs.

### Backend source of truth and mismatch

- Router registration in `apps/api/src/prodavan/api/v1/router.py:42-78` includes many routers beyond the committed OpenAPI draft.
- Chat stream/transcript behavior is implemented in `apps/api/src/prodavan/api/v1/agent.py:387-481`: SSE keepalive comments, structured `_error` frames, explicit `session_id`, `before_seq/after_seq`, 500-event clamp and `turn_in_progress`.
- `apps/api/openapi/openapi.yaml:1-8` explicitly says “Contract stub”, “implementation status: not started”; it only has skeletal paths through `:129` and basic schemas. `docs/05-backend/openapi-layout.md:1-35,154-201` describes an intended modular/codegen workflow that is not reflected in the current UI clients.
- Therefore React migration requires first producing a versioned, generated or manually maintained TypeScript contract from live FastAPI schemas/routes, including non-REST SSE and multipart/download semantics. Do not infer the full contract from `openapi.yaml` alone.

## What is reusable

### High-value, portable contracts

1. **Meta model and interpreter contract**: `ModuleMetaManifest`, view-kind dispatch, column/field definitions, action descriptors, scope/bind rules. Port as TypeScript discriminated unions plus runtime validation (Zod/Valibot or equivalent).
2. **Generic data adapter**: row envelope `{table_slug,row_id,session_id,body}`, list/CRUD, server paging/search/stock filter, cross-chat loading and action results. Keep API transport separate from React Query/Zustand state.
3. **Auth policy**: restore, expiry skew, single-flight refresh, 401 retry, logout and offline/expired distinction. Preserve the “UI calls Prodavan Auth Service, never Keycloak directly” boundary.
4. **Chat protocol/model**: `ChatBlock`, `ChatTurn`, transcript projection, SSE event types, append-only deltas, tool-call closure, keepalive/watchdog, reconnect/error status, transcript pagination and sidechain identity. `ChatSessionController` is behaviorally valuable but should be split into transport, reducer and hooks.
5. **Shell context model**: cabinet/project/session contexts, module placement, bind kind, active chat and selection persistence. React should make these explicit providers/stores, not hidden mutable fields.
6. **Presentation primitives**: theme tokens and `AppEntityCollection` semantics (responsive list/table, bounded columns, selection/actions, empty/loading/error states). `apps/flutter/lib/core/widgets/app_entity_collection.dart:16-186` is a clear reusable behavior spec even if its Flutter implementation is not.
7. **Localization and error envelope**: generated l10n catalog and RFC7807-like `{code,title,status,detail,trace_id}` handling should become typed shared packages.

### Low-value / do not port directly

- Flutter `Widget` trees, `MaterialPageRoute`, `ChangeNotifier`, `InheritedWidget`, mutable controller APIs and Flutter-specific platform branches.
- `collection_interpreter.dart` domain extensions for equipment, budget summaries, offers and document fields should be split into renderer plugins/field/action registries.
- `ProdavanApi` mutable `cabinetId/projectId` request context and dynamic `Map<String,dynamic>` return values should not become the React service pattern.
- Reference UI clones are research material only, not dependencies.

## Main anti-patterns / migration hazards

1. **Broken/stale source-of-truth links**: `employee-ui` is referenced but absent; screen architecture docs describe intended M00-M09/codegen layers that do not match the as-built tree.
2. **Contract drift**: giant handwritten Dart clients and dynamic JSON maps coexist with a skeletal OpenAPI file. This blocks safe generated TypeScript clients and makes endpoint/shape drift likely.
3. **Generic/domain coupling**: the collection/form interpreters import equipment, budgeting, remote DB, prompt and document widgets directly. A React rewrite that copies this shape will create a monolithic renderer.
4. **Dynamic typing at the boundary**: pervasive `Map<String,dynamic>`, string slugs and ad-hoc field names make invalid meta/API payloads runtime failures. Add schema validation and typed discriminated unions before rendering.
5. **Mutable ambient context**: `ProdavanApi` temporarily mutates `cabinetId`/`projectId` while issuing calls; `workContext` and global session/context singletons are convenient but make concurrent requests, tests and route transitions harder to reason about.
6. **Mixed state and transport**: controllers both fetch, mutate, normalize, poll, cache and notify widgets. React migration should separate API/query cache, domain reducers and presentation hooks.
7. **Navigation is runtime-computed but route state is widget-stack based**: `Navigator.push` plus shell indices and chat navigator keys are difficult to deep-link, refresh and test. React needs URL-addressable routes and explicit route guards.
8. **Polling and long-lived timers are distributed**: module pollers, auto refresh, presence heartbeat, chat live polling and SSE watchdogs require centralized cancellation, visibility handling and backoff.
9. **Legacy compatibility branches**: chat sidebar has a legacy fallback (`apps/flutter/lib/features/employee/cabinet_shell.dart:57-63,257+`), and API list decoders accept both bare arrays and `{items}`. Preserve only with an explicit deprecation/version policy.
10. **Very large files and feature folders**: `prodavan_api.dart`, collection/form interpreters and shell files concentrate unrelated responsibilities. Direct line-by-line translation will preserve complexity rather than architecture.
11. **Potential isolation hazards must remain backend-defined**: global SoT, `project_ids`, current/all chat scopes and action ACLs are not frontend conveniences. The React client must display and enforce server-provided permissions but cannot “fix” these semantics locally.

## Requirements for React + TypeScript migration

### Contract and platform prerequisites

- Establish live API source of truth: export FastAPI OpenAPI, reconcile it with `apps/flutter/lib/core/api/*.dart`, add missing schemas/routers, and CI-check API compatibility.
- Generate TypeScript DTOs/client for REST; hand-write a typed SSE client and multipart/download helpers. Model every error envelope and status code used by UI.
- Add runtime validation for server meta and critical API responses; reject invalid manifests with an auditable empty/error state.
- Version meta syntax and UI JSON. Define compatibility policy for `syntax_version`, `ui_json.version`, legacy sidebar/list envelopes and action response/file references.

### React application architecture

- URL-first routing with auth/cabinet/project/session guards and deep links for module view/row, chat and admin/company screens.
- Explicit providers/stores: auth session, work context, module runtime scope, chat selection and responsive shell. Avoid a mutable singleton API client; request context should be immutable per call.
- Query/cache layer with cancellation and invalidation for module rows, project lifecycle, chat transcript and sidebar; centralized timers/polling with page visibility and backoff.
- Meta interpreter as a registry: core renderers (`hub`, `collection`, `form`, `detail`, `profile_hub`) plus pluggable field, cell, row-action and scaffold-action handlers. Keep equipment/document/prompt/remote-DB widgets outside the core interpreter.
- Typed command/action layer for `invoke_action`, uploads, secret refs, downloads and lifecycle operations. Never put secrets in React state longer than necessary or in local persistence.
- Chat implemented as a reducer/state machine over typed block events; preserve append-only SSE, tool pairing, structured errors, transcript pagination, reload-mid-turn replacement and sidechain scopes.
- Responsive shell parity: desktop rail, narrow/mobile navigation, project tree chat rail, management/data hubs, settings and module placement.
- Accessibility and keyboard behavior should be specified explicitly during redesign; Flutter icon-first behavior is not itself a sufficient web UX contract.

### Migration strategy and verification

1. Freeze/describe live contracts and create TypeScript API package.
2. Port auth/session and a minimal shell with cabinet/project selection.
3. Port generic meta runtime against fixture manifests and mocked API; contract-test CRUD, scope, bind and action behavior.
4. Port employee project/chat workspace, including SSE/transcript reload and attachments.
5. Port company/admin screens using shared collection/form primitives.
6. Add product-specific module plugins (equipment, documents, prompts, remote catalogs).
7. Run parity tests against API fixtures and live backend: auth refresh, module invalid-meta, local/global bind, chat stream failure/keepalive, reload mid-turn, server-paged rows, file/secret upload and downloads.
8. Retire Flutter only after feature-by-feature acceptance; avoid a “big bang” rewrite because the module interpreter and chat have different risk profiles.

## Suggested first migration artifacts

- `packages/api-contracts` — generated TS DTOs + typed endpoint functions + SSE event schemas.
- `packages/meta-runtime` — manifest schemas, semantic validators, interpreter registry types, row/action contracts.
- `packages/chat-protocol` — block/event schemas and reducer tests.
- `apps/employee-web` — shell, auth, employee project/chat and module host.
- `apps/admin-web` / `apps/company-web` only if separate deployment/security boundaries are required; otherwise share one shell with contour-specific route guards.
- Fixture corpus copied from real module seeds/meta: `mod_equipment`, prompts, MCP, files, templates, documents, and representative invalid manifests.

## Key references

- Product behavior: `docs/PRODUCT.md:40-70,122-185`.
- Intended/as-built frontend architecture: `docs/04-frontend/architecture.md:1-17,151-205`.
- Screen inventory: `docs/04-frontend/screens-inventory.md:18-180`.
- Meta index and invariants: `docs/target/06-modules/meta-syntax/README.md:1-18,20-37,39-56,86-101`.
- Meta as-built/problem map: `docs/ARCHITECTURE/layers/01-meta-syntax-modules.md:9-38,54-111`.
- Live API router registration: `apps/api/src/prodavan/api/v1/router.py:42-78`.
- Chat SSE/transcript implementation: `apps/api/src/prodavan/api/v1/agent.py:387-481`.
- OpenAPI draft warning: `apps/api/openapi/openapi.yaml:1-8`.
