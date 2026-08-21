# Модули Prodavan (M00–M09)

> **LEGACY.** Канон модулей продукта: [docs/target/](../target/). M00–M09 — историческая нарезка.

| ID | Модуль | Зависимости | Кратко |
|----|--------|-------------|--------|
| M00 | [cabinets](M00-cabinets/README.md) | M08 | Кабинеты, profile packs, switch |
| M01 | [projects](M01-projects/README.md) | M00 | Проекты, workspace агента |
| M02 | [specs-kp](M02-specs-kp/README.md) | M01 | Спеки, pipeline, КП, equipment |
| M03 | [prompts](M03-prompts/README.md) | M00 | Промпты, MD editor, import/export |
| M04 | [catalogs](M04-catalogs/README.md) | M00 | БД прайсов, S4B system DB |
| M05 | [integrations](M05-integrations/README.md) | M00, M04 | Магазины, S4B trusted |
| M06 | [mcp](M06-mcp/README.md) | M00 | MCP registry, tools catalog |
| M07 | [agent](M07-agent/README.md) | M01, M06 | Чат, stream, history |
| M08 | [tenants](M08-tenants/README.md) | — | Users, RBAC, auth |
| M09 | [operations](M09-operations/README.md) | all | Audit, metrics, billing hooks |

## Шаблон файлов модуля

Каждая папка `M*/`:

- `README.md` — scope, links
- `domain.md` — entities, invariants, state machines
- `api.md` — REST/WS endpoints
- `persistence.md` — PostgreSQL tables
- `storage.md` — object store paths
- `mcp-tools.md` — инструменты агента
- `ui.md` — Flutter screens (icon-only)
- `security.md` — isolation, RBAC
- `checklist-implementation.md` — пошаговая реализация
- `checklist-review.md` — SOLID, DRY, security review
