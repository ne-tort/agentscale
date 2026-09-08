# Module — сущность (канон)

**Module (template)** — каталог метаданных в platform DB (`modules` + `module_meta_documents`).  
**Module Instance** — копия meta + data после **local** bind (`module_instances` + `module_instance_*`).  
**Global bind** — без новой instance; SoT резолвится вверх по иерархии.

Карта: [00-entities](../00-entities.md) · Meta: [07-scope-bindings](meta-syntax/07-scope-bindings.md). Продукт: [`docs/PRODUCT.md`](../../PRODUCT.md).

## Реестр (platform DB)

| Таблица | Смысл |
|---------|--------|
| `modules` | `id` (`mod_*`), `name`, `status` — template catalog |
| `module_meta_documents` | Template meta |
| `module_instances` | Local fork: `owner_kind` + `owner_id` + `module_id` + `parent_instance_id` |
| `module_instance_meta_documents` | Per-instance meta |
| `module_instance_data_rows` | Per-instance data (JSONB) |
| `module_company_grants` | Company link: `bind_kind`, `child_may_edit` |
| `module_cabinet_bindings` | MC: `bind_kind`, `child_may_edit` |
| `module_project_bindings` | MP (required for materialize): `bind_kind`, `child_may_edit` |

## Cascade

```text
Template → platform instance
         → company (local fork | global → platform SoT)
         → cabinet (local fork | global → parent SoT)
         → project (local leaf | global → parent SoT)
```

Materialize: только при явном MP; data из SoT instance; rules из template `materialize`.

## Привязки

| Правило | Канон |
|---------|-------|
| UI | Кнопки Local / Global + замок (`child_may_edit`) |
| Delete module | CASCADE template + instances + bindings |
| Delete cabinet/project | CASCADE owner instances + bindings |

## Не путать

| | |
|--|--|
| Module template | reusable meta catalog |
| Module instance | local-bind fork at an owner |
| Global bind | dependency on parent SoT, no fork |
| CabinetInstance | runtime shell (`cab_inst_*` schema) |

Дальше: [backend](backend.md) · **[meta-syntax](meta-syntax/README.md)**
