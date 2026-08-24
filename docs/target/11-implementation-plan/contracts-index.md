# Реестр контрактов между слоями

Единая точка «к чему цепляться». Детали — в файлах слоёв и каноне. Ломающее изменение → новая строка Compatibility + PR note.

| ID | Поставщик | Контракт | Потребители | Канон | Status |
|----|-----------|----------|-------------|-------|--------|
| C-API-HEALTH | L00 | `/api/v1/health` + error envelope | все | STUB | **live** |
| C-PRINCIPAL | L01 | JWKS → `Principal` | L04–L07 | [session](../10-identity-keycloak/session.md) | **live** |
| C-AUTH-CONFIG | L01 | GET `/auth/config` OIDC discovery | L05 Flutter | session | **live** (subset) |
| C-MEMBERSHIP | L01 | Company/Employee/Membership | L04, L05, L06 | session | **live** |
| C-HEADERS | L01 | `X-Cabinet-Id`, `X-Project-Id` | L05–L08 | session | **live** |
| C-INVITE | L01 | KC invite без password | L04 | session | **live** |
| C-UI-COLLECTION | L02 | `AppEntityCollection` | L04–L07 | [entity-collection](../07-ui-mobile-core/entity-collection.md) | **live** |
| C-UI-SELECTOR | L02 | `AppSelectorPage` | L04, L05 | [app-selector-page](../07-ui-mobile-core/app-selector-page.md) | **live** |
| C-UI-CONFIRM | L02 | `DangerConfirmPage` | L04–L08 HITL | 07 | **live** |
| C-KEY-ENTITY | L03 | `AiProviderKey` API (no secret) | L04, L08 | [02 domain](../02-ai-provider-keys/domain.md) | **live** (API + Admin UI subset) |
| C-KEY-RESOLVE | L03 | `resolve_credentials` | L08 | 02 + [admin-control](../08-agent-providers/admin-control-plane.md) | **live** |
| C-ADMIN-COMPANY | L04 | Admin API + Company org UI (employees, cabinets, summary) | L05, L06, L08 | [01](../01-platform-admin/), [03](../03-companies/) | **live** (Admin + Company UI subset) |
| C-QUOTA | L04 | `CompanyCabinetQuota` enforce | L06 | dynamic-cabinets | **live** (subset) |
| C-EMP-SHELL | L05 | Enter cabinet + shell host + project chat | L06 UI, L07, L08 | [04](../04-employees/) | **live** (subset) |
| C-INSTANCE | L06 | CabinetInstance CRUD/ACL | L05, L07 | [dynamic-cabinets](../05-cabinets/dynamic-cabinets.md) | **live** (subset) |
| C-META-DATA | L06 | Meta + rows API | L05 shell | [meta-and-ui](../05-cabinets/meta-and-ui.md) | **live** (subset) |
| C-CABINET-MCP | L06 | `cabinet.*` tools | L08 agent, packages | [mcp-contracts](../05-cabinets/mcp-contracts.md) | **live** (subset) |
| C-MCP-PKG | L06 | package deploy/bindings | L07 materialize, L08 | [mcp-packages](../05-cabinets/mcp-packages.md) | **live** (registry; sandbox L07) |
| C-BUNDLE | L06 | export/import zip | L05 UI, L09 starter | [bundle-format](../05-cabinets/bundle-format.md) | **live** (meta+data; packages skipped) |
| C-PROJECT | L07 | Project entity + lifecycle | L08, L09 | [project-contract](../06-projects-runtime/project-contract.md) | **live** (subset) |
| C-MATERIALIZE | L07 | FS layout + cwd | L08 | [container](../06-projects-runtime/container.md) | **live** (local-ws) |
| C-TRIGGERS | L07 | trigger ingress queue | L08, L09 | [triggers](../06-projects-runtime/triggers.md) | **live** (subset) |
| C-ATTACH | L07 | upload + list + ref validation | L08 ChatMessage | [chat-attachments](../06-projects-runtime/chat-attachments.md) | **live** (subset) |
| C-AGENT-PORT | L08 | `AgentProviderPort` | L09 chat | [adapter-port](../08-agent-providers/adapter-port.md) | **live** (fixture) |
| C-AGENT-EVENT | L08 | frozen `AgentEvent` | L09 UI, L04 metrics | adapter-port | **live** (subset) |
| C-USAGE | L08 | usage records | L04 metrics | [usage-metrics](../08-agent-providers/usage-metrics.md) | **live** (subset) |
| C-PROJECT-CHAT | L08 | `POST /chat`, `POST /chat/stream` (SSE), `GET /chat/transcript` | L05 UI, L09 | adapter-port | **live** (subset) |
| C-OBJECT-STORE | P0 / L00 | MinIO/S3 put/get/delete + object refs (no local SoT) | L07 attach/materialize, L06 packages | [13 stack](../13-platform-infra/stack.md) | **live** (subset: manager + attach/packages/materialize/wipes; live mount hole) |
| C-EVENT-BUS | P0 / L00 | Kafka envelopes: project triggers + platform events | L07, L06 SPI, L09 | [13](../13-platform-infra/), [triggers](../06-projects-runtime/triggers.md) | **live** (subset: dual-write after commit + kick\|dispatch consumer) |
| C-JOBS | P0 / L00 | Celery tasks: drain / dispatch / idle / rematerialize / wipe_* | L07, L08 | [13](../13-platform-infra/), [P0](P0-platform-infra.md) | **live** (subset: CLI + beat + task_id + job locks; Helm hole) |
| C-CACHE | P0 / L00 | Redis cache / short locks / Celery broker conventions | L00 core, workers | [13](../13-platform-infra/) | **live** (subset: get/set + lock + rate_limit; ingress + kick + job locks) |

## Compatibility log

| Дата | Контракт | Изменение | Major? |
|------|----------|-----------|--------|
| 2026-08-24 | P0 deploy / L02 UI | shared `ApiBase`; CI+local web build use ingress API_BASE; import_local_app_images_k3d | no |
| 2026-08-24 | P0 deploy / L03/L05 | seed: AI key+cabinet+project chat e2e; Celery shares API PVC; Flutter API_BASE ingress | no |
| 2026-08-24 | P0 deploy / GitOps | TF `bootstrap_gitops`; recover imports platform images + Argo; Job Sync hooks; seed_dev_identity | no |
| 2026-08-24 | P0 deploy / C-* | `infra/k3s/base/platform` Redis+MinIO+Redpanda StatefulSet+Celery; API ConfigMap P0 env | no |
| 2026-08-24 | C-EVENT-BUS | Redpanda PVC + headless advertise; avoid `dev-container`/`--mode empty` (fsync / invalid mode) | no |
| 2026-08-24 | C-OBJECT-STORE / C-JOBS | orphan blob GC (`list_child_prefixes` + admin/Celery/CronJob) | no |
| 2026-08-24 | C-CABINET / C-JOBS | orphan `cab_inst_*` schema GC (admin + Celery + CronJob) | no |
| 2026-08-24 | C-CACHE | shared `enforce_rate_limit`; admin ops + MCP call rate limits | no |
| 2026-08-24 | C-CABINET / C-OBJECT-STORE | `DELETE /cabinets/{id}` hard-delete archived (DROP SCHEMA + wipe) | no |
| 2026-08-24 | C-MATERIALIZE | Alembic `2026082317` + `POST /admin/projects/container-refs/backfill-object-ws` | no |
| 2026-08-24 | C-MATERIALIZE / C-OBJECT-STORE | new `container_ref=object-ws:`; dual-read `local-ws:` | no |
| 2026-08-24 | C-OBJECT-STORE / C-JOBS | project delete wipe verified + Celery `wipe_project_tree` retry | no |
| 2026-08-24 | P0 deploy | API/Celery sketches set REDIS/OBJECT_STORE/CELERY/KAFKA_REQUIRED | no |
| 2026-08-24 | C-OBJECT-STORE / C-JOBS | `delete_prefix_verified` + Celery `wipe_cabinet_packages` retry on archive | no |
| 2026-08-24 | C-JOBS / C-CACHE | Redis job lock coalesce inside trigger_drain / idle_pause_sweep | no |
| 2026-08-24 | P0 deploy | Postgres/Keycloak egress NetworkPolicy CIDR placeholders | no |
| 2026-08-24 | C-CACHE / C-TRIGGERS | ingress webhook/telegram rate limit (`INGRESS_RATE_LIMIT_PER_MINUTE`) | no |
| 2026-08-24 | C-EVENT-BUS / C-CACHE | Kafka drain-kick Redis lock coalesce | no |
| 2026-08-24 | C-JOBS | stable Celery `task_id` for dispatch_trigger / rematerialize_project | no |
| 2026-08-24 | C-OBJECT-STORE | archive `packages_wipe.remaining` verify after delete_prefix | no |
| 2026-08-24 | P0 deploy | egress NetworkPolicy + API Deployment sketch + cron pod labels | no |
| 2026-08-24 | C-EVENT-BUS | deferred Kafka publish after PG commit (no ghost on rollback) | no |
| 2026-08-24 | C-JOBS / C-API-HEALTH | WorkerManager broker ping; CELERY_REQUIRED readiness gate | no |
| 2026-08-24 | C-CACHE | acquire_lock / release_lock / rate_limit_allow facades | no |
| 2026-08-24 | C-OBJECT-STORE | list_prefix; archive packages_wipe observability | no |
| 2026-08-24 | P0 deploy | default-deny NetworkPolicy sketch | no |
| 2026-08-24 | C-OBJECT-STORE / L06 | archive wipe package prefix; replace deletes old artifact; read via object store | no |
| 2026-08-24 | C-API-HEALTH | readiness 503 when KAFKA_REQUIRED / OBJECT_STORE_REQUIRED | no |
| 2026-08-24 | C-EVENT-BUS | KafkaManager ensure topics on startup; k8s kafka-init Job | no |
| 2026-08-24 | P0 deploy | NetworkPolicy sketches for redis/minio/kafka | no |
| 2026-08-24 | C-OBJECT-STORE / L04 | admin storage_bytes includes cabinet package prefixes | no |
| 2026-08-24 | P0 deploy | k8s PVC sketches for redis/minio/kafka | no |
| 2026-08-24 | C-JOBS | Celery CLI import bootstrap; idle beat from `IDLE_PAUSE_WORKER_ENABLED` | no |
| 2026-08-24 | C-CACHE | quota peek; no HMAC in Redis; subscription flags recompute on hit | no |
| 2026-08-24 | C-OBJECT-STORE | `delete_prefix` + project tree wipe; k8s minio-init Job | no |
| 2026-08-24 | C-EVENT-BUS / C-JOBS / C-TRIGGERS | Kafka `KAFKA_CONSUMER_MODE=dispatch` → Celery `dispatch_trigger` + `claim_by_id` | no |
| 2026-08-24 | C-CACHE / C-ADMIN-POLICY | company agent policy + subscription Redis peek; invalidate on admin writes | no |
| 2026-08-24 | P0 deploy | k8s sketches redis/minio/kafka/celery under `deploy/k8s/` | no |
| 2026-08-24 | C-MATERIALIZE / C-OBJECT-STORE | ensure_package_tree hydrates sandbox from object-store zip | no |
| 2026-08-24 | C-CACHE / L00 | cache_get/set helpers; CORS via core.middleware.register_cors | no |
| 2026-08-24 | P0 deploy | stack compose: Redis/MinIO/Redpanda/celery-worker | no |
| 2026-08-24 | C-MATERIALIZE / C-OBJECT-STORE | materialize AGENTS/mcp/package.zip via ObjectStorageManager | no |
| 2026-08-24 | C-EVENT-BUS / C-JOBS | Kafka consumer opt-in kicks Celery trigger_drain (debounce) | no |
| 2026-08-24 | C-EVENT-BUS | KafkaManager + EventEnvelope; dual-write from platform emit + trigger enqueue | no |
| 2026-08-24 | C-JOBS | WorkerManager + Celery tasks; in-process loop skipped when Celery executor active | no |
| 2026-08-24 | C-OBJECT-STORE / C-ATTACH / C-MCP-PKG | ObjectStorageManager; new refs `object://`; legacy `file://` readable | no |
| 2026-08-24 | C-CACHE / L00 | RedisManager + LifespanManager wiring; ready checks when REDIS_URL set | no |
| 2026-08-24 | C-OBJECT-STORE / C-EVENT-BUS / C-JOBS / C-CACHE | planned P0 platform-infra contracts (Kafka/MinIO/Celery/Redis) | no |
| 2026-08-24 | C-TRIGGERS / C-PROJECT | enqueue+claim refuse when paused (except `project.prepare`); webhook → PROJECT_PAUSED | no |
| 2026-08-23 | C-MCP-PKG / C-MATERIALIZE / L09 | rematerialize on package deploy/disable; IDLE_PAUSE_WORKER_ENABLED | no |
| 2026-08-23 | C-ADMIN-POLICY / C-PROJECT / L09 | idle_pause_after_hours + sweep; attachment MIME sniff | no |
| 2026-08-23 | C-ADMIN / L09 | release_gate_check + platform events admin UI + employee.disabled assert | no |
| 2026-08-23 | C-CABINET-MCP / C-PLATFORM-EVENTS / L09 | stdin/stdout JSON handler result; lazy suspend commit; suspend E2E; SSE cancel UX | no |
| 2026-08-23 | C-TRIGGERS / C-ATTACH | outbox-lite lease on project_triggers; Flutter inbox list/delete | no |
| 2026-08-23 | C-ADMIN-SUBSCRIPTION / C-PLATFORM-EVENTS | lazy natural-expiry company.suspended via subscription gate | no |
| 2026-08-23 | C-PROJECT / C-ATTACH / C-TRIGGERS | company_subscription on project GET; attachment gate; drain fail queued on suspend | no |
| 2026-08-23 | C-CABINET-MCP / C-PROJECT | MCP_PLATFORM_EVENT_INVOKE zip handler; project create COMPANY_SUSPENDED gate | no |
| 2026-08-23 | C-ADMIN-COMPANY / C-TRIGGERS | subscription transition events (dedupe); trigger enqueue gate; company.reactivated | no |
| 2026-08-23 | C-CABINET-MCP / platform events | company-scoped SPI fan-out; manifest platform_events stub audit | no |
| 2026-08-23 | C-TRIGGERS / C-ADMIN-COMPANY / C-PROJECT-CHAT | telegram HMAC ingress; company.suspended emit; COMPANY_SUSPENDED on agent create/send | no |
| 2026-08-23 | C-TRIGGERS / C-ATTACH / platform events / C-CABINET-MCP | cabinet SPI deliver; webhook HMAC ingress; DELETE attachment; employee.disabled emit | no |
| 2026-08-23 | C-TRIGGERS / C-ATTACH / platform events | platform_events bus; telegram.message; magic sniff; chat text optional w/ attachments | no |
| 2026-08-23 | C-TRIGGERS / C-PROJECT-CHAT / C-ATTACH | chat.regenerate + schedule/webhook dispatch; worker advisory lock; transcript attachment_refs + Flutter chips | no |
| 2026-08-23 | C-ATTACH / C-PROJECT-CHAT | list attachments; validate refs on chat (id or storage_ref) | no |
| 2026-08-23 | C-ATTACH / C-ADMIN-COMPANY | company max_attachment_mb + upload extension allowlist; project limits in API | no |
| 2026-08-23 | C-PROJECT / C-TRIGGERS / C-KEY-RESOLVE | PATCH project agent_provider; admin trigger drain; opt-in TRIGGER_WORKER_* | no |
| 2026-08-23 | C-MATERIALIZE / C-META-DATA | workspace-docs (AGENTS) API + materialize source; vault:// secret_ref routing | no |
| 2026-08-23 | C-MATERIALIZE / C-TRIGGERS | opt-in local MCP package spawn; trigger dispatch drain `?max=` | no |
| 2026-08-23 | C-MATERIALIZE / C-MCP-PKG | materialize prepares package sandbox run.json + mcp.json sandbox metadata | no |
| 2026-08-23 | C-TRIGGERS / C-PROJECT-CHAT | integration tests for trigger dispatch + platform_fallback agent session | no |
| 2026-08-23 | C-KEY-RESOLVE | platform_fallback uses unbound key pool; ai_key.expired audit on lazy expire | no |
| 2026-08-23 | C-KEY-ENTITY | ai_key.* audit events + GET /admin/ai-keys/audit-events | no |
| 2026-08-23 | C-AUTH-CONFIG / C-EMP-SHELL | OIDC PKCE (AppAuth + desktop loopback); secure token storage; refresh on restore | no |
| 2026-08-23 | C-META-DATA / C-CABINET-MCP | PATCH meta column type/metadata; cabinet.columns.update MCP | no |
| 2026-08-23 | C-META-DATA / C-CABINET-MCP | meta HTTP audit; hard delete archived table; cabinet.tables.delete | no |
| 2026-08-23 | C-AUTH-CONFIG / C-PRINCIPAL | GET /auth/config; Flutter LoginPage + SessionStore | no |
| 2026-08-23 | C-CABINET-MCP | meta_audit_events on MCP call + GET audit-events | no |
| 2026-08-23 | C-EMP-SHELL | ContourSelectorPage multi-company | no |
| 2026-08-23 | C-META-DATA / C-CABINET-MCP | meta table archive+rename; cabinet.tables.archive MCP | no |
| 2026-08-23 | C-EMP-SHELL | CabinetTableSettingsPage + CabinetMetaViewEditPage | no |
| 2026-08-23 | C-EMP-SHELL | Custom tabs UI (CabinetMetaTabsPage) + shell reload epoch | no |
| 2026-08-23 | C-META-DATA | PATCH/DELETE views/tabs/columns; duplicate table slug 409 | no |
| 2026-08-23 | C-EMP-SHELL | CabinetColumnAddPage for meta column mutate | no |
| 2026-08-23 | C-META-DATA | POST columns/views/tabs + GET views; duplicate table slug 409 | no |
| 2026-08-23 | C-EMP-SHELL | CabinetTableCreatePage (L05 full-page) | no |
| 2026-08-23 | C-PROJECT-CHAT / C-AGENT-EVENT | HITL tool_approval_request + approve/deny API | no |
| 2026-08-23 | C-USAGE / C-ADMIN-COMPANY | max_cost_usd_month agent policy + budget enforce | no |
| 2026-08-23 | C-BUNDLE | E2E starter equipment-procurement import | no |
| 2026-08-23 | C-BUNDLE | import applies views/tabs; equipment-procurement starter shipped | no |
| 2026-08-23 | C-META-DATA | list_tabs includes table_slug; row edit full-page | no |
| 2026-08-23 | C-META-DATA | `GET meta/tables/{slug}` columns for empty tables | no |
| 2026-08-23 | C-EMP-SHELL | ProjectCreatePage full-page wiring | no |
| 2026-08-23 | C-ADMIN-COMPANY | Admin Bundles tab + starter catalog API | no |
| 2026-08-23 | C-META-DATA | L05 tables tab upsert/delete + row HTTP client | no |
| 2026-08-23 | C-BUNDLE | export save to zip via FilePicker | no |
| 2026-08-23 | C-ADMIN-COMPANY | subscription edit UI on company create/detail | no |
| 2026-08-23 | C-META-DATA | `GET meta/tabs` includes `view_slug` for L05 routing | no |
| 2026-08-23 | C-PROJECT-CHAT | transcript collapse includes `role=tool` for tool_call | no |
| 2026-08-23 | C-EMP-SHELL | CabinetTabHost interpreters: projects/tables/tools | no |
| 2026-08-23 | C-PROJECT-CHAT | SSE `POST /chat/stream` + L05 streaming workspace | no |
| 2026-08-23 | C-ADMIN-COMPANY / C-USAGE | L08 AgentBudgetService token limits on policy | no |
| 2026-08-23 | C-KEY-ENTITY | Admin Flutter AI keys list/create/bind; list_keys includes company_ids | no |
| 2026-08-23 | C-ADMIN-COMPANY | Company contour UI + employees/summary API | no |
| 2026-08-23 | C-ADMIN-COMPANY | L04 Admin Flutter shell (companies, metrics, quotas, policy) | no |
| 2026-08-23 | C-PROJECT-CHAT | transcript + platform user_message persist; list sessions | no |
| 2026-08-23 | C-EMP-SHELL / C-PROJECT-CHAT | L05 chat workspace + L08 chat_turn endpoint | no |
| 2026-08-23 | C-AGENT-PORT / C-AGENT-EVENT / C-USAGE | L08: port, persist, fixture Cursor adapter | no |
| 2026-08-23 | C-PROJECT / C-MATERIALIZE / C-TRIGGERS / C-ATTACH | L07: project runtime + local-ws materialize | no |
| 2026-08-23 | C-QUOTA / C-ADMIN-COMPANY | L04: quotas+policy API; L06 enforce | no |
| 2026-08-23 | C-MCP-PKG | L06: mcp.package-v1 validate+deploy/list/disable/export | no |
| 2026-08-23 | C-BUNDLE | L06: cabinet.bundle-v1 export/import → new schema | no |
| 2026-08-23 | C-CABINET-MCP | L06: dispatcher info/tables/tabs/rows + SQL ban | no |
| 2026-08-23 | C-META-DATA | L06: rows query/upsert/delete HTTP | no |
| 2026-08-23 | C-INSTANCE / C-MATERIALIZE | L06: instance registry, materialize stub | no |
| 2026-08-23 | C-KEY-* | L03: admin AI keys + resolve_credentials (file secret_ref) | no |
| 2026-08-23 | C-PRINCIPAL…C-INVITE | L01: Principal, membership, headers, invite shape | no |
| 2026-08-23 | C-UI-* | L02: EntityCollection, Selector, DangerConfirm live | no |
| 2026-08-23 | C-API-HEALTH | L00: health meta + AppError problem+json | no |
| (start) | * | Initial registry from target canon | — |

## Правила

1. Потребитель зависит от **ID контракта**, не от файловых путей реализации.
2. Fake/mocks в тестах обязаны реализовывать тот же ID.
3. Удаление поля из frozen `AgentEvent` / `cabinet.*` = major + миграция доков канона.
4. Факт «что торчит в коде» дублируется в as-built карточках [`12-layer-docs`](../12-layer-docs/); при `live` обновить и карточку, и эту таблицу / [map](../12-layer-docs/map.md).
