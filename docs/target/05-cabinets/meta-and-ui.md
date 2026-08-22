# Meta catalog & dynamic UI

Как из таблиц и JSON метаданных строится интерфейс. Канон UI: [07](../07-ui-mobile-core/).

## Принцип

```text
meta.tables + meta.columns + meta.views + meta.tabs
        ↓
CabinetShell (Flutter)
        ↓
AppEntityCollection / dynamic AppForm / empty states
```

Нет доменных экранов «ПоставщикиScreen» в коде продукта — есть renderer + meta.

## TableDefinition (data schema)

| Field | Описание |
|-------|----------|
| `slug` | stable id (`suppliers`) |
| `label` | «Разрешённые поставщики» |
| `storage_kind` | `physical` \| `json_document` |
| `columns[]` | name, type, required, unique?, ref? |

### Column types (allowlist v1)

`text` · `number` · `bool` · `datetime` · `json` · `enum` · `ref` · `file_ref`

## ViewDefinition (UI schema)

Минимальный `ui_json` (эволюция допустима, version field):

```json
{
  "version": 1,
  "kind": "collection",
  "title_field": "name",
  "subtitle_fields": ["status", "region"],
  "columns": [
    { "field": "name", "label": "Имя" },
    { "field": "status", "label": "Статус", "tone_map": { "blocked": "danger" } }
  ],
  "primary_action": { "kind": "create_row" },
  "row_tap": { "kind": "open_form", "form_view": "suppliers_form" }
}
```

Laconic: labels короткие; никаких instructional paragraphs в meta.

## TabDefinition

| Field | Описание |
|-------|----------|
| `id` | uuid |
| `title` | 1–3 слова («Поставщики») |
| `order` | int |
| `view_id` | link to view |
| `system` | bool — base tabs нельзя удалить агентом без flag |

Base system tabs: Projects, Chat, Context (prompts/skills/rules/MCP/seeds), **Tables**, **Tools**.

## Flutter interpreters

| kind | Renderer |
|------|----------|
| `collection` | `AppEntityCollection` (list/table per breakpoints) |
| `form` | Dynamic fields → `AppTextField` / selectors |
| `board` | later |

Невалидный `ui_json` → EmptyState «Метаданные» + факт ошибки (без простыни), строка в audit.

## Refresh после агента

v1: pull-to-refresh + invalidate on MCP success callback to UI.  
v2: websocket `cabinet.meta.changed`.
