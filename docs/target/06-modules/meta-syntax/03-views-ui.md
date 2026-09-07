# Views — UI schema

View = **как показать** данные таблицы. Отделён от schema (Retool / RJSF pattern).

## ViewDefinition

```json
{
  "slug": "suppliers_list",
  "table_slug": "suppliers",
  "label": "Поставщики",
  "kind": "collection",
  "enabled": true,
  "ui_json": {
    "version": 1,
    "kind": "collection",
    "title_field": "name",
    "subtitle_fields": ["status", "region"],
    "columns": [
      { "field": "name", "label": "Имя", "width": 200 },
      { "field": "status", "label": "Статус", "tone_from": "status" },
      { "field": "region", "label": "Регион" }
    ],
    "primary_action": {
      "kind": "create_row",
      "label": "Добавить"
    },
    "row_tap": {
      "kind": "open_form",
      "view": "suppliers_form"
    },
    "row_actions": [
      { "kind": "invoke_action", "action": "export_row_pdf", "icon": "picture_as_pdf" }
    ],
    "empty": {
      "title": "Нет поставщиков",
      "action": { "kind": "create_row", "label": "Добавить" }
    },
    "toolbar": [
      { "kind": "refresh" },
      { "kind": "invoke_action", "action": "sync_external", "icon": "sync" }
    ]
  }
}
```

| Field | Description |
|-------|-------------|
| `slug` | Stable view id |
| `table_slug` | Data source |
| `label` | Internal / debug |
| `kind` | Top-level mirror of `ui_json.kind` |
| `enabled` | If false — not renderable |
| `ui_json` | Interpreter payload |

## ui_json.kind — interpreters

### `collection` → AppEntityCollection

| ui_json field | Maps to |
|---------------|---------|
| `title_field` | `AppEntityRow.title` |
| `subtitle_fields` | joined → `subtitle` |
| `columns[].field` | `cells[id]` |
| `columns[].label` | `AppEntityColumn.label` |
| `columns[].width` | fixed width |
| `columns[].tone_from` | lookup column `ui.tone_map` |
| `primary_action.kind=create_row` | toolbar add → form (only when **no** `inline_add`) |
| `inline_add` | `AppInlineAddField` — canonical inline create (see below) |
| `row_tap.kind=open_form` | `Navigator.push` form view |
| `row_tap.kind=open_view` | `Navigator.push` any view (e.g. profile hub, nested collection) |
| `context_bind` | Map child body field → `contextRowId` (parent row). Example: `{"line_id": "contextRowId"}` filters/creates children for that parent |
| `row_filter` | Static equality filters on body fields (combined with `context_bind`) |
| `selection` | Single-select radio among rows (see below) |
| `empty` | `EmptyPlaceholder` — laconic per-table (`{"ru":"Нет MCP","en":"No MCP"}`) |
| `empty.title` | MetaLabel for EmptyPlaceholder |
| `empty.icon` | Material icon name (same map as hub tiles / `metaIconFromName`) |
| `scaffold.title` | Optional app bar title (MetaLabel); default — none |

**List/table mode:** page provides `AppCollectionViewModeButton`; meta не задаёт mode.

**Create paths (mutually exclusive on collection):**

| Config | UI |
|--------|-----|
| `inline_add` present | `AppInlineAddField` above table; no toolbar `+`, no empty-state create button |
| `primary_action` only | toolbar `AppIconButton` + optional empty action → opens form |

### MetaLabel (UI strings)

Labels in meta may be:

```json
"title": "Добавить MCP package"
"title": { "ru": "Добавить файл", "en": "Add file" }
"title": { "l10n": "metaAddNew", "args": { "item": "MCP package" } }
```

Used for `inline_add.title`, `columns[].label`, `empty.title`, `scaffold.title`. Legacy `inline_add.label` is an alias for `title`.

Interpreter: [`meta_label.dart`](../../../../apps/flutter/lib/features/meta/meta_label.dart) → core widgets ([`AppInlineAddField`](../../../../apps/flutter/lib/core/widgets/app_inline_add_field.dart), [`AppEntityCollection`](../../../../apps/flutter/lib/core/widgets/app_entity_collection.dart)).

### `inline_add`

```json
"inline_add": {
  "field": "name",
  "title": "Добавить MCP package",
  "hintText": "Добавить MCP package"
}
```

| Field | Maps to |
|-------|---------|
| `field` | body key set on create |
| `title` | collapsed row label (`AppInlineAddField.title`) |
| `hintText` | expanded TextField hint (default = `title`) |
| `label` | deprecated alias for `title` |

Hairline divider under field is built into `AppInlineAddField` (same as Company employees / Admin lists).

When `context_bind` is set, inline create copies bound parent id into the named field (not only legacy `profile_id`).

### Master–detail (`open_view` + `context_bind`)

```json
"row_tap": { "kind": "open_view", "view": "offers_for_line" }
```

Child collection:

```json
{
  "slug": "offers_for_line",
  "table_slug": "found_offers",
  "kind": "collection",
  "ui_json": {
    "version": 1,
    "kind": "collection",
    "title_field": "title",
    "context_bind": { "line_id": "contextRowId" },
    "selection": {
      "kind": "single",
      "field": "is_selected",
      "action": "select_offer_primary"
    }
  }
}
```

Parent row id is passed as `contextRowId` into the child interpreter. Filter: body[`line_id`] == contextRowId. Inline add sets the same field.

### `selection` (single among siblings)

| Field | Meaning |
|-------|---------|
| `kind` | `single` (MVP) |
| `field` | bool column on row (`is_selected`) |
| `action` | ActionDefinition id (`data.select_row`) invoked with `row_id` |

Radio in row leading; tap → `POST .../actions/{action}/invoke?row_id=`.

### `form` → preference fields

```json
{
  "version": 1,
  "kind": "form",
  "table_slug": "suppliers",
  "mode": "edit",
  "fields": [
    { "column": "name", "widget": "value" },
    { "column": "status", "widget": "choice" },
    { "column": "approved", "widget": "switch" },
    { "column": "spec_file", "widget": "file" }
  ],
  "sections": [
    { "title": "Основное", "fields": ["name", "status"] },
    { "title": "Файлы", "fields": ["spec_file"] }
  ],
  "save": { "kind": "seamless" }
}
```

| `mode` | Use |
|--------|-----|
| `create` | After primary_action create_row |
| `edit` | Row tap |
| `read` | read_only columns enforced |

| `save.kind` | Behavior |
|-------------|----------|
| `seamless` | Each preference `onSave` → PATCH row (default) |
| `submit` | Explicit Save button (multi-field create only) |

| Field extra | Behavior |
|-------------|----------|
| `read_only` | Preference disabled (display-only) |
| `visible_when` | Show field only when condition matches current row body |
| `icon` | Leading icon on preference (`metaIconFromName`) |
| `widget: column_map` | Map source headers → canonical schema slots |
| `widget: pause_toggle` | Action row (pause/resume) instead of switch; optional `pause_label` / `resume_label` / icons; `accent: warning` |
| `widget: type_ref_picker` | Nav to types collection; warning accent when empty; `pick_view` + `empty_style` |
| `widget: schema_attrs` | Dynamic text fields from type `fields_json` into `attrs` map |
| `widget: fields_schema_editor` | Edit type `fields_json` ({key,label}) with inline add |

Collection `selection.set_on_context` (type pick): radio writes fields onto `contextRowId` row (no action invoke); optional `also_copy`, `clear_fields`, `pop_after`.


`visible_when` shapes:

```json
{ "field": "status", "eq": "ready" }
{ "field": "status", "in": ["ready", "error"] }
{ "field": "error", "not_empty": true }
```

`column_map` field shape:

```json
{
  "column": "column_map",
  "widget": "column_map",
  "source_columns_from": "columns_json",
  "schema": [
    {
      "key": "title",
      "label": {"ru": "Название", "en": "Title"},
      "required": true,
      "synonyms": ["title", "name", "наименование"]
    }
  ]
}
```

UI: preference-tile with chevron → dedicated mapping page (not an inline stack of dropdowns on the settings form).
Collection column envelope sources (not body fields):

```json
{ "field": "added_at", "label": {"ru": "Добавлено"}, "source": "row.created_at" }
```

Supported: `row.created_at`, `row.updated_at`.

### `hub` → navigation list

```json
{
  "version": 1,
  "kind": "hub",
  "items": [
    { "title": "Список", "icon": "list", "target": { "kind": "view", "view": "suppliers_list" } },
    { "title": "Настройки", "icon": "settings", "target": { "kind": "view", "view": "suppliers_settings" } }
  ]
}
```

Maps to `AppNavPreference` rows — progressive disclosure without modals.

### `detail` (read-only form)

Same as `form` with all fields `read_only: true` or `mode: read`.

### `board` (future)

Kanban — `group_by` column, drag-drop changes enum column.

## Navigation targets (uniform)

```json
{ "kind": "view", "view": "suppliers_form" }
{ "kind": "tab", "tab_id": "…" }
{ "kind": "external", "url": "https://…" }
{ "kind": "project", "project_id": "{current}" }
```

## Tone and status

Column-level `ui.tone_map` + view `tone_from`:

```json
"status": "blocked"  →  AppListItem tone danger (via meta, not hardcode)
```

## Filters and search (v1.1)

```json
"filters": [
  { "column": "status", "op": "eq", "value": "active", "label": "Активные" }
],
"search": { "fields": ["name", "region"] }
```

MVP: client-side filter on loaded rows; v2: query params to API.

## Error display

Invalid `ui_json`:

```text
EmptyPlaceholder(title: "Метаданные", subtitle: "<parse error code>")
+ audit log entry cabinet.ui.invalid_view
```

## Product view kinds (Prompts / Files / MCP)

| `ui_json.kind` | Interpreter | Notes |
|----------------|-------------|-------|
| `profile_hub` | ProfileHubInterpreter | Nav blocks; radio only when opened without profile context |
| `form` + `widget: markdown_editor` | MarkdownEditorField | AGENTS.md, prompt items |
| `form` + `widget: file_upload` | FileUploadField | `file_ref` via `/cabinets/{id}/content/upload` |
| `form` + `widget: project_multiselect` | ProjectMultiselectField | `project_ids` column; empty = all projects |
| `form` + `widget: column_map` | ColumnMapField | map source headers → canonical schema |
| `form` + `widget: pause_toggle` | AppNavPreference | bool pause/resume action (warning accent) |
| `form` + `widget: type_ref_picker` | TypeRefPickerField | open types pick collection |
| `form` + `widget: schema_attrs` | SchemaAttrsField | dynamic text attrs from type schema |
| `form` + `widget: fields_schema_editor` | FieldsSchemaEditorField | edit type fields_json |
| `form` + `fields[].icon` | preference leading icon | any value/switch/choice field |

Collection extras: `inline_add`, `row_filter`, `context_bind.profile_id=contextRowId`.

**Page titles:** shell hosts do not inject tab titles into `AppScaffold`. Nested pages resolve title via `resolveViewScaffoldTitle`:

1. `ui_json.scaffold.title` (MetaLabel) — preferred for collections/hubs
2. `ui_json.title` — form, detail, and any view that sets it

Fallback in host: `view.label` → view slug. Management modules often open the collection as the tab root (tab title), so nested hub tiles **must** set `scaffold.title` (or `title`) or the AppBar shows the slug.

`file_upload` field extras: `accept`, `subtitle_from` (body column for preference subtitle, e.g. `row_count`), `empty_style: "warning"` (title warning color + hidden subtitle until file present).

Дальше: [tabs-navigation](04-tabs-navigation.md)
