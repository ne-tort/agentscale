# Cabinet — сущность (канон)

Кабинет — **общее рабочее пространство** (registry + PG schema для данных модулей).  
Сотрудники одной компании, назначенные в кабинет, работают **вместе**: все проекты кабинета видны всем назначенным сотрудникам.

Meta-шаблоны (tables/columns/views/tabs) живут в **Module**; кабинет хранит только **данные** bound modules.  
Карта: [00-entities](../00-entities.md) · Modules: [06-modules](../06-modules/entity.md).

## Шаблон vs экземпляр компании

| Сущность | `owner_scope` | Кто видит | Смысл |
|----------|---------------|-----------|--------|
| **Шаблон** (Admin) | `platform` | только Platform Admin | каталог: модули, grants на компании |
| **Workspace copy** | `company` | Company + назначенные Employee | материализованная копия шаблона для одной компании |

При grant Admin→Company на шаблон платформа **создаёт копию** (`template_cabinet_id` → шаблон).  
Company UI и Employee operate работают с **копией**, не с platform-шаблоном.

## Реестр (platform DB)

| Поле | Смысл |
|------|--------|
| `id` | `cab_*` |
| `name` | имя |
| `owner_scope` | `platform` (шаблон) \| `company` (workspace) |
| `owner_company_id` | компания workspace при `owner_scope=company` |
| `template_cabinet_id` | nullable; ссылка на platform-шаблон для копии |
| `company_id` | legacy anchor (primary grant); nullable на шаблонах |
| `owner_employee_id` | nullable; audit при employee create (не ownership) |
| `max_projects` | nullable; лимит активных проектов **в этом кабинете** (null = без лимита, кроме квот компании) |
| `schema_name` | PG schema (`cab_inst_…`) |
| `status` | `active` / `archived` / `deleted` |
| timestamps | |

### Grant tables

- `cabinet_company_grants` — N:M cabinet ↔ company (`mode`, `status`); на шаблоне — кто получил workspace; на копии — anchor компании
- `cabinet_employee_assignments` — N:M cabinet ↔ employee (`role`, `status`)

## Project в кабинете

- Проект привязан **только к cabinet_id** (и `company_id` орг-контекста).
- `owner_employee_id` / `created_by_employee_id` — **метаданные** (кто создал); не ownership, не ACL.
- Удаление Employee **не** удаляет его проекты (`ON DELETE SET NULL` на creator FK).
- Удаление Cabinet (soft) → soft_delete всех проектов кабинета.

## Runtime data (PG schema `cab_inst_*`)

При bind module → `module_installations` + `module_data_rows` (данные per cabinet, изолированы схемой).

| Таблица | Смысл |
|---------|--------|
| `module_installations` | какие modules установлены в этом кабинете |
| `module_data_rows` | `(module_id, table_slug, row_id)` + JSONB body — **данные**, не шаблон |

## Связи

```text
Module ──N:M──► CabinetInstance ──has──► Project ──1:1──► ProjectContainer
```

**Lifecycle** ([00-lifecycle.md](../00-lifecycle.md)):

| Op | Эффект |
|----|--------|
| `archive` (pause) | виден; write gate; projects freeze/stop |
| soft_delete (`DELETE`) | `status=deleted`; soft_delete projects (**no wipe**); schema **keep** |
| `restore` | → archived (paused); без cascade revive projects |
| `purge` | wipe soft-deleted projects + DROP schema + delete row |

## Не путать

| | |
|--|--|
| Cabinet (шаблон) | platform catalog; grant → provision copy |
| Cabinet (workspace) | общее рабочее пространство компании |
| Module | reusable meta catalog (platform DB) |
| Project | единица работы в workspace; общие для всех сотрудников кабинета |

Дальше: [assignment](assignment.md) · [backend](backend.md) · [materialize](materialize-from-meta.md).
