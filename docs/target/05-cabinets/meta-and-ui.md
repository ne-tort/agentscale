# Meta catalog & dynamic UI

Как из таблиц и JSON метаданных строится интерфейс. Канон UI: [07](../07-ui-mobile-core/).

> **Полная спецификация синтаксиса:** [06-modules/meta-syntax](../06-modules/meta-syntax/README.md) — tables, columns, views, tabs, actions, materialize, MCP, правила для ИИ.

## Принцип

Meta slugs (`tables`, `columns`, `views`, `tabs`) хранятся в **Module** (platform DB, shared template).  
Runtime данные — в `cab_inst_*.module_data_rows` (per cabinet).

```text
module_meta_documents (shared template)
        ↓ bind
CabinetShell (Flutter) reads template + cabinet data rows
        ↓
AppEntityCollection / dynamic preference fields / empty states
```

Нет доменных экранов «ПоставщикиScreen» в коде продукта — есть renderer + meta.

## Краткая справка (legacy summary)

Детали — в [meta-syntax](../06-modules/meta-syntax/). Здесь — orientation.

### TableDefinition (data schema)

| Field | Описание |
|-------|----------|
| `slug` | stable id (`suppliers`) |
| `label` | «Разрешённые поставщики» |
| `storage_kind` | `physical` \| `json_document` |
| `columns[]` | name, type, required, unique?, ref? |

### Column types (allowlist v1)

`text` · `number` · `bool` · `datetime` · `json` · `enum` · `ref` · `file_ref`

### ViewDefinition (UI schema)

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

### TabDefinition

| Field | Описание |
|-------|----------|
| `id` | uuid |
| `title` | 1–3 слова («Поставщики») — **nav label** |
| `order` | int |
| `view_id` / `view_slug` | link to view |
| `system` | bool — base tabs нельзя удалить агентом без flag |

Base system tabs: Projects, Chat, Context, **Tables**, **Tools**.

## Flutter interpreters

| kind | Renderer |
|------|----------|
| `collection` | `AppEntityCollection` |
| `form` | Dynamic fields → preference kit |
| `hub` | `AppNavPreference` list |
| `board` | later |

## Refresh после агента

v1: pull-to-refresh + invalidate on MCP success.  
v2: websocket `cabinet.data.changed`.
