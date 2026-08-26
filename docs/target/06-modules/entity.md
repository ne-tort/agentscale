# Module — сущность (канон)

**Module** — переиспользуемый каталог метаданных (meta tables/columns/views/tabs) для кабинетов.  
Не runtime instance: **шаблон** в platform DB (`module_meta_documents`); **данные** — в `cab_inst_*.module_data_rows`.

Карта: [00-entities](../00-entities.md) · Meta slugs: [05-cabinets/meta-and-ui](../05-cabinets/meta-and-ui.md).

## Реестр (platform DB)

| Таблица | Смысл |
|---------|--------|
| `modules` | `id` (`mod_*`), `name`, `status`, timestamps |
| `module_meta_documents` | `module_id`, `slug`, `body` JSONB — канон slugs: `tables`, `columns`, `views`, `tabs` |
| `module_cabinet_bindings` | N:M module ↔ cabinet |
| `module_project_bindings` | N:M module ↔ project (только если module уже bound к `project.cabinet_id`) |

## Привязки

```text
Module ──N:M──► CabinetInstance
Module ──N:M──► Project (precondition: cabinet grant exists)
```

| Правило | MVP |
|---------|-----|
| Admin UI bind | cabinets only |
| Project bind | API only; project.cabinet_id must have active MC row |
| Delete module | CASCADE meta + bindings only; cabinets/projects **не** удаляются |
| Delete cabinet | CASCADE MC rows; auto-revoke MP for projects in that cabinet |
| Delete project | CASCADE MP row |

## Не путать

| | |
|--|--|
| Module | reusable meta catalog (platform DB) |
| CabinetInstance | runtime shell + `cab_inst_*` schema |
| Legacy code-pack «module» | deprecated; см. [05-cabinets/dynamic-cabinets](../05-cabinets/dynamic-cabinets.md) |

Дальше: [backend](backend.md) · **[meta-syntax](meta-syntax/README.md)** — полная спецификация синтаксиса
