# Seed rows (optional meta slug)

Предзаполнение **module instance** data rows при создании/upsert platform instance и local fork. Шаблон `seed_rows` в meta — SoT для **миграций** (insert-only); интерактивное редактирование данных (включая MCP zip) идёт через **live instance** UI, не через этот документ.

## Document

Slug: `seed_rows`  
Body:

```json
{
  "items": [
    {
      "table_slug": "notes",
      "row_id": "seed_welcome",
      "body": {
        "title": "Welcome",
        "status": "draft"
      }
    }
  ]
}
```

Допустима и bare-array форма: `[ { "table_slug", "row_id", "body" }, … ]`.

| Field | Rule |
|-------|------|
| `table_slug` | `[a-z][a-z0-9_]{0,63}` — должен существовать в `tables` |
| `row_id` | stable id `[a-zA-Z0-9_-]{1,64}` (не `row_` random) |
| `body` | object — поля по ColumnDefinition |

## Runtime

1. Alembic `upsert_product_modules` → `_apply_seed_rows_to_instances` → `module_instance_data_rows` **`ON CONFLICT DO NOTHING`** (никогда не затирает body).
2. Local bind → `fork_instance` копирует instance meta+data (включая `file_ref`) в child.
3. API bootstrap (`SeedMcpBootstrapService`) кладёт MCP zip в object store и пишет `file_ref` **только если пуст**.
4. Admin/company «Предзаполнение» и cabinet hubs правят **instance rows** через owner/cabinet data API + owner-scoped content upload.

Legacy: при MC install ещё может писаться `cab_*.module_data_rows` (insert-only) — канон SoT для UI/агента — `module_instances`.

## vs column `default` vs materialize

| Механизм | Что делает |
|----------|------------|
| `columns[].default` | Значение поля при **новой** строке |
| `seed_rows` | Готовые строки в instance DB при upsert/fork (insert-only) |
| `materialize` | Файлы в Project workspace / Pod (не DB rows) |

## Authoring

```bash
# Template seed (migrations / product packs)
PUT /api/v1/admin/modules/{module_id}/meta/documents/seed_rows

# Live instance data (UI «Предзаполнение»)
GET/POST/PATCH /api/v1/admin/modules/{module_id}/data/{table_slug}
POST /api/v1/admin/modules/{module_id}/content/upload
```
