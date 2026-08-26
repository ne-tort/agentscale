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
| `primary_action.kind=create_row` | toolbar add → form view or inline create |
| `row_tap.kind=open_form` | `Navigator.push` form view |
| `empty` | `EmptyPlaceholder` — laconic |

**List/table mode:** page provides `AppCollectionViewModeButton`; meta не задаёт mode.

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

Дальше: [tabs-navigation](04-tabs-navigation.md)
