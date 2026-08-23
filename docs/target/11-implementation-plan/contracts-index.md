# Реестр контрактов между слоями

Единая точка «к чему цепляться». Детали — в файлах слоёв и каноне. Ломающее изменение → новая строка Compatibility + PR note.

| ID | Поставщик | Контракт | Потребители | Канон | Status |
|----|-----------|----------|-------------|-------|--------|
| C-API-HEALTH | L00 | `/api/v1/health` + error envelope | все | STUB | **live** |
| C-PRINCIPAL | L01 | JWKS → `Principal` | L04–L07 | [session](../10-identity-keycloak/session.md) | **live** |
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
| C-ATTACH | L07 | attachment_refs | L08 ChatMessage | [chat-attachments](../06-projects-runtime/chat-attachments.md) | **live** (subset) |
| C-AGENT-PORT | L08 | `AgentProviderPort` | L09 chat | [adapter-port](../08-agent-providers/adapter-port.md) | **live** (fixture) |
| C-AGENT-EVENT | L08 | frozen `AgentEvent` | L09 UI, L04 metrics | adapter-port | **live** (subset) |
| C-USAGE | L08 | usage records | L04 metrics | [usage-metrics](../08-agent-providers/usage-metrics.md) | **live** (subset) |
| C-PROJECT-CHAT | L08 | `POST /chat`, `POST /chat/stream` (SSE), `GET /chat/transcript` | L05 UI, L09 | adapter-port | **live** (subset) |

## Compatibility log

| Дата | Контракт | Изменение | Major? |
|------|----------|-----------|--------|
| 2026-08-23 | C-BUNDLE | L04 starter catalog + L05 import from catalog | no |
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
