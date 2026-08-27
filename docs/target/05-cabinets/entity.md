# Cabinet — сущность (канон)

Кабинет — **runtime shell** (registry + PG schema для данных модулей).  
Meta-шаблоны (tables/columns/views/tabs) живут в **Module**; кабинет хранит только **данные** bound modules.  
Карта: [00-entities](../00-entities.md) · Modules: [06-modules](../06-modules/entity.md).

## Реестр (platform DB)

| Поле | Смысл |
|------|--------|
| `id` | `cab_*` |
| `name` | имя |
| `owner_scope` | `platform` \| `company` |
| `owner_company_id` | creator company при `owner_scope=company` |
| `company_id` | legacy anchor (primary grant); nullable |
| `owner_employee_id` | nullable; audit / employee create |
| `schema_name` | PG schema (`cab_inst_…`) |
| `status` | `active` / `archived` (pause, виден) / `deleted` (soft, скрыт) |
| timestamps | |

### Grant tables

- `cabinet_company_grants` — N:M cabinet ↔ company (`mode`, `status`)
- `cabinet_employee_assignments` — N:M cabinet ↔ employee (`role`, `status`)

## Runtime data (PG schema `cab_inst_*`)

При bind module → `module_installations` + `module_data_rows` (данные per cabinet, изолированы схемой).

| Таблица | Смысл |
|---------|--------|
| `module_installations` | какие modules установлены в этом кабинете |
| `module_data_rows` | `(module_id, table_slug, row_id)` + JSONB body — **данные**, не шаблон |

Шаблон meta читается из `module_meta_documents` (platform DB) через binding.

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
| Cabinet | registry + per-cabinet data schema |
| Module | reusable meta catalog (platform DB) |
| module_data_rows | runtime rows per cabinet |
| Project | единица работы в кабинете |

Дальше: [assignment](assignment.md) · [backend](backend.md) · [materialize](materialize-from-meta.md).
