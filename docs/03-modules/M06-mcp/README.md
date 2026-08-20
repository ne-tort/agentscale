# M06 — MCP Registry

Модуль **реестра MCP-серверов**: установка, включение/отключение, доставка tools агенту с **фильтром по профилю задачи**. Центральный слой между runtime агента (M07) и доменными серверами (M04 pipeline, M05 integrations).

## Назначение

| Задача | Решение |
| --- | --- |
| Какие MCP доступны кабинету | Registry per tenant/cabinet |
| Install / enable / disable | Lifecycle API + UI |
| Миграция с Commerce | tools-catalog.md — полный маппинг |
| Шум в контексте модели | Profile tool filter |

## Архитектура

```text
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│ M07 Agent   │────▶│ M06 MCP Registry │────▶│ MCP Server proc │
│ (session)   │     │ (filter+route)   │     │ catalog/s4b/... │
└─────────────┘     └──────────────────┘     └─────────────────┘
                            │
                    ┌───────┴───────┐
                    │ Profile YAML  │
                    │ kp / audit    │
                    └───────────────┘
```

## Серверы Prodavan (v1)

| server_id | namespace | источник |
| --- | --- | --- |
| `prodavan-catalog` | `catalog.*` | Commerce `commerce-search` |
| `prodavan-s4b` | `s4b.*` | Commerce `commerce-s4b` |
| `prodavan-offers` | `offers.*`, `specs.*` | Commerce `commerce-offers` |
| `prodavan-pipeline` | `pipeline.*` | Commerce `commerce-pipeline` |
| `prodavan-equipment` | `equipment.*` | Commerce `commerce-equipment` |
| `prodavan-integrations` | `integrations.*` | M05 |
| `prodavan-extract` | `extract.*` | Commerce `commerce-extract` |

## Документация

| Файл | Содержание |
| --- | --- |
| [domain.md](domain.md) | McpServer, McpInstallation, Profile |
| [api.md](api.md) | Registry REST API |
| [persistence.md](persistence.md) | Таблицы registry |
| [storage.md](storage.md) | Server bundles, config files |
| [mcp-tools.md](mcp-tools.md) | **Полный каталог tools + Commerce mapping** |
| [ui.md](ui.md) | Экран MCP в админке |
| [security.md](security.md) | Sandboxing, supply chain |
| [checklist-implementation.md](checklist-implementation.md) | |
| [checklist-review.md](checklist-review.md) | |

## Инварианты

1. Агент видит **только enabled** servers для cabinet + profile filter.
2. Discovery при старте сессии; изменение registry → `/reset` или hot-reload (configurable).
3. Нет глобального «включить все MCP» для tenant без explicit opt-in.
4. Community MCP — отдельный approval workflow (disabled by default).
