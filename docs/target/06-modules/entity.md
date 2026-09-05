# Module — сущность (канон)

**Module (template)** — каталог метаданных в platform DB (`modules` + `module_meta_documents`).  
**Module Instance** — независимая копия meta + data после bind (`module_instances` + `module_instance_*`).

Карта: [00-entities](../00-entities.md) · Meta slugs: [05-cabinets/meta-and-ui](../05-cabinets/meta-and-ui.md).

## Реестр (platform DB)

| Таблица | Смысл |
|---------|--------|
| `modules` | `id` (`mod_*`), `name`, `status`, timestamps — template catalog |
| `module_meta_documents` | Template meta: `module_id`, `slug`, `body` JSONB |
| `module_instances` | Fork: `owner_kind` (`platform`/`company`/`cabinet`/`project`) + `owner_id` + `module_id` + `parent_instance_id` |
| `module_instance_meta_documents` | Per-instance meta copy |
| `module_instance_data_rows` | Per-instance data rows (JSONB body) |
| `module_cabinet_bindings` | N:M module ↔ cabinet (triggers cabinet fork) |
| `module_company_grants` | Visibility + company fork |
| `module_project_bindings` | Optional allowlist module ↔ project |

## Cascade (copy-on-bind)

```text
Template → platform instance
         → company instance (on grant)
         → cabinet instance (on MC bind)
         → project instance (on create / ensure; leaf for UI + materialize)
```

Parent не видит мутации child. Re-sync from parent — отдельный явный API (вне MVP).

**Materialize (MVP):** data/profile rows from **project instance**; materialize **rules** still from template slug `materialize`. Admin/company meta PUT mirrors into owner instance; Alembic seed upsert refreshes **platform** instance meta only.

## Привязки

| Правило | MVP |
|---------|-----|
| Admin UI bind | cabinets; fork cabinet instance |
| Company grant | fork company instance |
| Project | ensure project instance from cabinet; hubs = selected project |
| Delete module | CASCADE template meta + instances + bindings |
| Delete cabinet/project | CASCADE owner instances |

## Legacy

`cab_inst_*.module_data_rows` + row `project_ids` — migration/fallback. Канон изоляции — **project instance**, не фильтр shared cabinet rows.

## Не путать

| | |
|--|--|
| Module template | reusable meta catalog |
| Module instance | editable fork at an owner |
| CabinetInstance | runtime shell + `cab_inst_*` schema (legacy data layer) |

Дальше: [backend](backend.md) · **[meta-syntax](meta-syntax/README.md)**
