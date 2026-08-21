# Cabinets — backend

## Регистрация модулей

In-process registry (как сейчас `cabinets/registry.py`):

```text
profile_id → CabinetModule implementation
```

Целевое расширение: pack install из registry (legacy `platform-extensibility.md`), но контракт модуля — этот документ.

## SPI (базовый, сохраняем дух текущего кода)

Интерфейс (логически):

- `health()` / `manifest()`
- `migrate(ctx)`
- `execute_command(ctx, name, payload)`
- `execute_query(ctx, name, payload)`
- `on_platform_event(ctx, event)`
- `materialize_project(ctx, project_id)` — **новое** явное требование target

HTTP фасад платформы: `/api/v1/cabinets/{id}/spi/...` (legacy уже есть).

## БД

| Уровень | Содержимое |
|---------|------------|
| Platform | Company, Employee, Project id, cabinet instance id, grants |
| Cabinet | Доменные таблицы модуля (SQLite/Postgres per cabinet policy) |

## MCP

Manifest кабинета перечисляет разрешённые MCP server ids. Gateway платформы режет остальное.
