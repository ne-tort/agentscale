# Cabinets — backend

## Регистрация модулей

In-process registry (target layout):

```text
profile_id → CabinetModule (SPI implementation)
```

Файлы только под `prodavan/cabinets/<profile_id>/` (+ опционально `cabinets/_base/` kit).  
Платформа вызывает registry; **не** импортирует services pack напрямую ([packaging](packaging.md)).

Catalog row (Admin): `profile_id`, version, enablement — отдельно от in-process load.

## SPI

Полный surface: [module-contract.md](module-contract.md).

HTTP фасад платформы (thin): `/api/v1/cabinets/{cabinet_id}/spi/...` — name dispatch → SPI.  
Доменные «удобные» REST-пути допустимы только как **тонкие** aliases на commands/queries, не как второй слой бизнес-логики в platform.

## БД (один cluster, свои таблицы)

| Уровень | Где | Кто мигрирует |
|---------|-----|----------------|
| Platform | Platform schema / Alembic core | `apps/api/alembic` |
| Cabinet | **Своя schema** `cab_<profile_id>` (канон) или строго префикс `cab_<profile>_…` | `CabinetModule.migrate` / pack SQL |

Правила:

1. Кабинет **не** создаёт таблицы в platform schema.
2. Кабинет **не** читает/пишет schema другого кабинета.
3. Project / company / employee ids — из `SpiContext` (platform), не копировать org-таблицы в cabinet.
4. Instance-level data: строки cabinet schema фильтруются `cabinet_instance_id` / `company_id` по контракту pack (RLS опционально позже; логическая изоляция обязательна уже сейчас).

Пример:

```text
platform:  companies, employees, projects, company_cabinet_grants, …
cab_generic_assistant:  prompt_versions, skill_blobs, mcp_configs, …
cab_equipment_procurement:  runs, lineitems, offers, … (+ может иметь свои копии context tables base kit, не shared mutable)
```

## MCP

Manifest → allowlist server ids. Platform gateway/agent materialize режет остальное.  
Конфиг MCP, которым управляет пользователь в UI, хранится в **cabinet schema**, в workspace попадает через `materialize_project`.

## Secrets

| Secret | Где |
|--------|-----|
| AI provider keys | Только platform (02) → resolve в runtime |
| Доменные (S4B и т.п.) | Cabinet vault / cabinet schema encrypted — pack-owned |

## Связь

- [packaging.md](packaging.md) — изоляция и эволюция  
- [module-contract.md](module-contract.md)
