# M04 — Каталоги (Catalogs)

Модуль **M04-catalogs** управляет пользовательскими прайс-БД (upload, query, lifecycle) и **системными** базами, включая S4B для профиля **electronics-procurement only**.

## Назначение

| Задача | Описание |
| --- | --- |
| User catalogs | Загрузка xlsx/csv/sqlite, индексация, MCP search |
| System databases | Неудаляемые платформенные БД (S4B cache) |
| Credential vault | Безопасное хранение S4B credentials per tenant |
| Cred states | missing_credentials / credentials_valid / credentials_invalid / rate_limited |
| Scope | Cabinet-level catalogs; query from M02 runs |

## S4B — только electronics

| Правило | Детали |
| --- | --- |
| Availability | `capabilities.integrations.s4b === true` |
| System DB | `s4b-cache` virtual, non-deletable |
| Other cabinets | **NO s4b** — tool, API, UI hidden |
| on_order | Never exposed in search results |

Подробнее: [storage.md — System databases](storage.md#system-databases).

## Зависимости

```text
M00 capabilities gate
M04-catalogs
  → M02 search cascade (catalog first)
  → MCP commerce-search, commerce-s4b
```

## Документация

| Файл | Содержание |
| --- | --- |
| [domain.md](domain.md) | Catalog, SystemDatabase, CredState |
| [api.md](api.md) | CRUD catalogs, credentials |
| [persistence.md](persistence.md) | vault, catalog registry |
| [storage.md](storage.md) | user catalogs + **system-databases** |
| [mcp-tools.md](mcp-tools.md) | list_databases, query |
| [ui.md](ui.md) | catalogs admin, S4B creds |
| [security.md](security.md) | vault encryption |
| [checklist-implementation.md](checklist-implementation.md) | |
| [checklist-review.md](checklist-review.md) | |

## Статус

0.1.0-draft
