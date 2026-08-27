# Seed rows (optional meta slug)

Предзаполнение **cabinet** `module_data_rows` при MC bind. Шаблон остаётся shared; строки появляются **в каждом кабинете**, куда модуль привязан.

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

На `ModuleMaterializeService.install`:

1. Ensure schema + `module_installations`
2. Read `module_meta_documents` where `slug=seed_rows`
3. `INSERT … ON CONFLICT (module_id, table_slug, row_id) DO NOTHING`

Re-bind / re-install **не** перетирает изменённые пользователем строки с тем же `row_id`.  
Unbind → delete all `module_data_rows` for module (включая seed).

## vs column `default` vs materialize

| Механизм | Что делает |
|----------|------------|
| `columns[].default` | Значение поля при **новой** строке |
| `seed_rows` | Готовые строки в БД кабинета при bind |
| `materialize` | Файлы в Project workspace / Pod (не DB rows) |

## Authoring

```bash
# Admin API
PUT /api/v1/admin/modules/{module_id}/meta/documents/seed_rows
```

Без заморочек: отредактировать JSON в admin module meta editor и сохранить slug `seed_rows`.
