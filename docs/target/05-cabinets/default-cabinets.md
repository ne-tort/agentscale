# Default cabinets & starters

## Platform bootstrap — кабинет «Базовый»

При первом старте API (после migrate) `PlatformBootstrapService` idempotent создаёт:

| Entity | ID | Содержание |
|--------|-----|------------|
| Cabinet | `cab_basic` | «Базовый», `base_template=basic`, `company_grant_scope=all` |
| Module bindings | — | `mod_prompts`, `mod_mcp`, `mod_files` |

Marker: `platform_bootstrap.platform_bootstrap.v1`.

## Base / basic template (новые кабинеты)

При создании кабинета с `base_template` **`basic`** или **`base`** автоматически bind:

- `mod_prompts` — профили промптов, AGENTS.md, rules/skills/…
- `mod_mcp` — zip MCP packages (local registry)
- `mod_files` — файлы в workspace через content upload

Admin-owned и employee-owned cabinets — одинаково через `CabinetInstanceService`.

## Grant «Все компании»

`cabinet_instances.company_grant_scope`:

| Value | Effect |
|-------|--------|
| `selected` | Только явные grants в `cabinet_company_grants` |
| `all` | Кабинет виден всем компаниям без per-row grant |

`cab_basic` seed — `all`.

## Starter bundles (не code modules)

| Bundle | Бывший смысл |
|--------|----------------|
| `equipment-procurement` starter | Спека/поиск/КП как seed tables + views + declarative MCP wrappers |
| others | По мере появления |

Ставятся через **Import** из platform catalog или файла — тот же [bundle-format](bundle-format.md).

## Больше не канон

Отдельные деревья `cabinets/electronics_procurement` в коде приложения как способ добавить домен.

Example modules (`mod_example_*`) удалены — заменены product seed в Alembic `2026082711`.
