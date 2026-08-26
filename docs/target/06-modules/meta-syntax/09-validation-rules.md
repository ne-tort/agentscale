# Validation rules

Машиночитаемые ограничения для platform validator и **ИИ-автора**.

## Global

| Rule | Constraint |
|------|------------|
| `syntax_version` | integer ≥ 1 |
| Slugs | `^[a-z][a-z0-9_]{0,63}$` |
| Labels | 1–80 chars; no newlines |
| JSON depth | ≤ 12 |
| Array max length | tables/views/tabs ≤ 100 per module |
| Columns per table | ≤ 40 |

## Slug document bodies

| Slug | Root type |
|------|-----------|
| `tables` | `array` of TableDefinition |
| `columns` | `array` of ColumnDefinition |
| `views` | `array` of ViewDefinition |
| `tabs` | `array` of TabDefinition |
| `actions` | `array` of ActionDefinition |
| `materialize` | `array` of MaterializeRule |
| `mcp_tools` | `array` of McpToolDefinition |

## Referential integrity

| From | Must exist |
|------|------------|
| `columns[].table_slug` | `tables[].slug` |
| `views[].table_slug` | `tables[].slug` |
| `views[].ui_json.columns[].field` | column `name` OR allow dynamic |
| `tabs[].view_slug` | `views[].slug` |
| `ref.table` | `tables[].slug` |
| `row_tap.view` | `views[].slug` |
| `action.params.source.table_slug` | `tables[].slug` |
| `ui_json.row_actions[].action` | `actions[].id` |

Validator runs on:

- Admin PUT meta document  
- MCP `cabinet.views.upsert` etc.  
- Import bundle  

## Row body validation

On data write:

```text
FOR each column in table:
  IF required AND missing → 422
  IF type mismatch → 422 (or coerce text→number)
  IF enum AND value not in values → 422
  IF ref AND row_id not found → 422
  IF file_ref AND object missing → 422
  IF unique AND duplicate → 409
```

Unknown keys: **strip + warn** (mode `lenient`, default) or **reject** (mode `strict`, per table flag).

## ui_json validation

| Check | |
|-------|---|
| `kind` in allowlist | collection, form, hub, detail |
| `title_field` exists as column | if collection |
| `columns[].field` duplicate-free | |
| No HTML in labels | |
| `empty.title` ≤ 40 chars | laconic |

## Materialize validation

| Check | |
|-------|---|
| `target.workspace_path` no `..` | path traversal |
| Must start with allowed roots | AGENTS.md, prompts/, rules/, skills/, cabinet-seed/, inbox/, out/, packages/ |
| `when` events from allowlist | |

## Bind-time validation

When module bound to cabinet:

| Check | |
|-------|---|
| No `table.slug` collision with other bound modules | |
| `storage_kind=physical` tables — DDL migration plan | future |

## Error codes

| Code | Meaning |
|------|---------|
| `META_SLUG_INVALID` | bad slug format |
| `META_REF_MISSING` | broken reference |
| `META_UI_INVALID` | ui_json parse |
| `META_SCOPE_DENIED` | project binding |
| `ROW_VALIDATION` | data write |
| `ACTION_FAILED` | runtime action |

Дальше: [ai-authoring-guide](10-ai-authoring-guide.md)
