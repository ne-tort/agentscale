# Tables and columns

Schema-слой meta: **что хранится** и **как валидировать** строки.

## TableDefinition

```json
{
  "slug": "suppliers",
  "label": "Поставщики",
  "description": "Разрешённые поставщики",
  "storage_kind": "json_document",
  "enabled": true,
  "scope": {
    "projects": "all"
  },
  "audit": {
    "created_by": true,
    "updated_at": true
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `slug` | string | ✓ | Stable id: `[a-z][a-z0-9_]{0,63}` |
| `label` | string | ✓ | UI: 1–4 слова |
| `description` | string | | Tooltip / empty hint (коротко) |
| `storage_kind` | enum | ✓ | `json_document` \| `physical` |
| `enabled` | bool | | default `true`; `false` → скрыта из UI, MCP read-only |
| `scope.projects` | enum | | `all` \| `bound` \| `none` — см. [scope-bindings](07-scope-bindings.md) |
| `primary_key` | string | | default `id` (implicit row_id in json_document) |
| `icon` | string | | Material icon name (optional tab leading) |

### storage_kind

| Value | Data plane | Backend |
|-------|------------|---------|
| `json_document` | `module_data_rows.body` JSONB | **MVP** — уже реализовано |
| `physical` | PG table `{schema}.data_{module}_{table}` | Future: DDL from columns |

**Рекомендация v1:** `json_document` для гибкости; `physical` — когда нужен SQL/reporting.

## ColumnDefinition

Хранится в slug `columns` как массив (нормализованная форма — **предпочтительна для ИИ**):

```json
{
  "table_slug": "suppliers",
  "name": "status",
  "label": "Статус",
  "type": "enum",
  "required": true,
  "unique": false,
  "read_only": false,
  "hidden": false,
  "default": "active",
  "enum": {
    "values": ["active", "blocked", "pending"],
    "labels": { "active": "Активен", "blocked": "Заблокирован", "pending": "Ожидание" }
  },
  "ui": {
    "tone_map": { "blocked": "danger", "pending": "warning" }
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `table_slug` | string | FK → TableDefinition.slug |
| `name` | string | Field key in row `body` |
| `label` | string | Preference / column header |
| `type` | ColumnType | см. ниже |
| `required` | bool | Validation on write |
| `unique` | bool | Unique within table per cabinet |
| `read_only` | bool | UI disabled; MCP may still write if policy allows |
| `hidden` | bool | Not in default view columns |
| `default` | any | Default on create_row (UI/API should merge missing keys from column default) |

### Column defaults (практика)

Чтобы поле всегда имело значение при создании строки — достаточно править meta JSON:

```json
{
  "table_slug": "notes",
  "name": "status",
  "type": "enum",
  "default": "draft",
  "enum": { "values": ["draft", "done"] }
}
```

Admin: `PUT /admin/modules/{id}/meta/documents/columns`.  
Это **не** создаёт строки — только default при insert. Для готовых строк см. slug `seed_rows` в [overview](01-overview.md).

| `enabled_when` | Condition | Conditional visibility — см. [scope-bindings](07-scope-bindings.md) |

## ColumnType allowlist

| type | Row JSON | UI widget | MCP notes |
|------|----------|-----------|-----------|
| `text` | string | `AppValuePreference` | max_length optional |
| `number` | number | `AppValuePreference` + digitsOnly | min/max optional |
| `bool` | boolean | `AppSwitchPreference` | immediate save |
| `datetime` | ISO8601 string | `AppValuePreference` / subscription style | |
| `json` | object/array | multiline `AppValuePreference` | schema-free sub-object |
| `enum` | string | `AppChoicePreference` | requires `enum.values` |
| `ref` | string (row_id) | `AppChoicePreference` | requires `ref.table` |
| `file_ref` | FileRef object | `FileUploadField` → Content Service | requires `file` block |
| `secret_ref` | SecretRef object | masked upload (target) | requires `secret` block — see [13-container-env-secrets](13-container-env-secrets.md) |

### FileRef object shape (as-built)

Canonical shape after upload via Content Service (`POST /cabinets/{id}/content/upload`):

```json
{
  "asset_id": "ca_abc123",
  "version_id": "cbv_def456",
  "storage_key": "blobs/abc123def456",
  "filename": "spec.pdf",
  "content_type": "application/pdf",
  "size": 102400,
  "sha256": "…"
}
```

Legacy `object_key` in older docs — **deprecated**; use `storage_key`. Full pipeline: [12-content-file-pipeline](12-content-file-pipeline.md).

Column `file` block (upload constraints only):

```json
{
  "accept": ["application/pdf", ".xlsx"],
  "max_bytes": 10485760
}
```

Materialize path is declared in slug `materialize` (`format: copy_blob`), not auto from column meta in MVP.

### Text → `.md` without upload

Column `type: text` + view widget `markdown_editor` → materialize rule `format: raw`:

```json
{
  "table_slug": "prompts",
  "name": "body_md",
  "type": "text",
  "ui": { "widget": "markdown_editor" }
}
```

See [12-content-file-pipeline](12-content-file-pipeline.md) Phase C.

### ref block

```json
{
  "ref": {
    "table": "suppliers",
    "display_field": "name",
    "on_delete": "restrict"
  }
}
```

| `on_delete` | Behavior |
|-------------|----------|
| `restrict` | Block delete if references exist |
| `set_null` | Clear ref field |
| `cascade` | Delete dependent rows (dangerous — audit) |

## Inline columns (alternative)

Допустимо вложить `columns[]` в TableDefinition для **малых** модулей (≤5 columns):

```json
{
  "slug": "notes",
  "label": "Заметки",
  "storage_kind": "json_document",
  "columns": [
    { "name": "title", "type": "text", "required": true },
    { "name": "body", "type": "text", "required": false }
  ]
}
```

Platform merge: inline + normalized `columns` slug → unified catalog.

## Row identity

| storage_kind | Row id |
|--------------|--------|
| `json_document` | `row_id` from API (`row_{hex}`) |
| `physical` | PK column |

API path: `/cabinets/{cab}/modules/{mod}/data/{table_slug}/{row_id}`

## Validation pipeline

```text
write request body
  → resolve TableDefinition + ColumnDefinitions
  → strip unknown keys (warn audit) OR reject (strict mode)
  → type coercion (string→number where safe)
  → required / unique / ref integrity
  → file_ref: verify asset exists in Content Service (target — gap P-META-FILE-02)
  → persist module_data_rows
  → emit cabinet.data.changed (future websocket)
```

## Anti-patterns

| ✗ | ✓ |
|---|---|
| Column name `id` as user field | Use `name`, `code`; system `row_id` separate |
| 50 columns in one table | Split tables + `ref` |
| `json` for structured known fields | Explicit columns |
| Free-form slug `Suppliers-List` | `suppliers_list` |

Дальше: [views-ui](03-views-ui.md)
