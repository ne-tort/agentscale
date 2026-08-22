# Реестр контрактов между слоями

Единая точка «к чему цепляться». Детали — в файлах слоёв и каноне. Ломающее изменение → новая строка Compatibility + PR note.

| ID | Поставщик | Контракт | Потребители | Канон | Status |
|----|-----------|----------|-------------|-------|--------|
| C-API-HEALTH | L00 | `/api/v1/health` + error envelope | все | STUB | **live** |
| C-PRINCIPAL | L01 | JWKS → `Principal` | L04–L07 | [session](../10-identity-keycloak/session.md) | planned |
| C-MEMBERSHIP | L01 | Company/Employee/Membership | L04, L05, L06 | session | planned |
| C-HEADERS | L01 | `X-Cabinet-Id`, `X-Project-Id` | L05–L08 | session | planned |
| C-INVITE | L01 | KC invite без password | L04 | session | planned |
| C-UI-COLLECTION | L02 | `AppEntityCollection` | L04–L07 | [entity-collection](../07-ui-mobile-core/entity-collection.md) | planned |
| C-UI-SELECTOR | L02 | `AppSelectorPage` | L04, L05 | [app-selector-page](../07-ui-mobile-core/app-selector-page.md) | planned |
| C-UI-CONFIRM | L02 | `DangerConfirmPage` | L04–L08 HITL | 07 | planned |
| C-KEY-ENTITY | L03 | `AiProviderKey` API (no secret) | L04, L08 | [02 domain](../02-ai-provider-keys/domain.md) | planned |
| C-KEY-RESOLVE | L03 | `resolve_credentials` | L08 | 02 + [admin-control](../08-agent-providers/admin-control-plane.md) | planned |
| C-ADMIN-COMPANY | L04 | Company CRUD, quotas, policy | L05, L06, L08 | [01](../01-platform-admin/), [03](../03-companies/) | planned |
| C-QUOTA | L04 | `CompanyCabinetQuota` enforce | L06 | dynamic-cabinets | planned |
| C-EMP-SHELL | L05 | Enter cabinet + shell host | L06 UI, L07 | [04](../04-employees/) | planned |
| C-INSTANCE | L06 | CabinetInstance CRUD/ACL | L05, L07 | [dynamic-cabinets](../05-cabinets/dynamic-cabinets.md) | planned |
| C-META-DATA | L06 | Meta + rows API | L05 shell | [meta-and-ui](../05-cabinets/meta-and-ui.md) | planned |
| C-CABINET-MCP | L06 | `cabinet.*` tools | L08 agent, packages | [mcp-contracts](../05-cabinets/mcp-contracts.md) | planned |
| C-MCP-PKG | L06 | package deploy/bindings | L07 materialize, L08 | [mcp-packages](../05-cabinets/mcp-packages.md) | planned |
| C-BUNDLE | L06 | export/import zip | L05 UI, L09 starter | [bundle-format](../05-cabinets/bundle-format.md) | planned |
| C-MATERIALIZE | L06→L07 | hook + FS layout | L07, L08 | [container](../06-projects-runtime/container.md) | planned |
| C-PROJECT | L07 | Project entity + lifecycle | L08, L09 | [project-contract](../06-projects-runtime/project-contract.md) | planned |
| C-TRIGGERS | L07 | trigger ingress queue | L08, L09 | [triggers](../06-projects-runtime/triggers.md) | planned |
| C-ATTACH | L07 | `attachment_refs` | L08 ChatMessage | [chat-attachments](../06-projects-runtime/chat-attachments.md) | planned |
| C-AGENT-PORT | L08 | `AgentProviderPort` | L09 chat | [adapter-port](../08-agent-providers/adapter-port.md) | planned |
| C-AGENT-EVENT | L08 | frozen `AgentEvent` | L09 UI, L04 metrics | adapter-port | planned |
| C-USAGE | L08 | usage records | L04 metrics | [usage-metrics](../08-agent-providers/usage-metrics.md) | planned |

## Compatibility log

| Дата | Контракт | Изменение | Major? |
|------|----------|-----------|--------|
| 2026-08-23 | C-API-HEALTH | L00: health meta + AppError problem+json | no |
| (start) | * | Initial registry from target canon | — |

## Правила

1. Потребитель зависит от **ID контракта**, не от файловых путей реализации.
2. Fake/mocks в тестах обязаны реализовывать тот же ID.
3. Удаление поля из frozen `AgentEvent` / `cabinet.*` = major + миграция доков канона.
4. Факт «что торчит в коде» дублируется в as-built карточках [`12-layer-docs`](../12-layer-docs/); при `live` обновить и карточку, и эту таблицу / [map](../12-layer-docs/map.md).
