# Example: Data-only module

Модуль **без UI tabs** — только schema + MCP для агента.

## Intent

| | |
|--|--|
| Use case | Internal lookup table (SKU codes, API endpoints) |
| UI | None — data via MCP / future admin JSON editor |
| Employee | Does not see tab; agent reads/writes |

## `tables`

```json
[
  {
    "slug": "sku_map",
    "label": "SKU map",
    "storage_kind": "json_document",
    "enabled": true,
    "scope": { "projects": "bound" }
  }
]
```

## `columns`

```json
[
  { "table_slug": "sku_map", "name": "sku", "label": "SKU", "type": "text", "required": true, "unique": true },
  { "table_slug": "sku_map", "name": "vendor", "label": "Vendor", "type": "text", "required": true },
  { "table_slug": "sku_map", "name": "meta", "label": "Meta", "type": "json", "required": false }
]
```

## No `tabs` / `views`

Validator allows module with tables+columns only if:

- `scope.projects=bound` OR admin flag `data_only: true` on module (future), OR
- Auto UI hidden (`visibility: hidden` default for missing views)

Agent uses platform MCP:

```text
cabinet.rows.query({ table: "sku_map", filter: { sku: "PN-123" } })
cabinet.rows.upsert({ table: "sku_map", row: { sku, vendor, meta } })
```

## `mcp_tools`

```json
[
  {
    "id": "sku_lookup",
    "name": "sku_lookup",
    "kind": "rows_query",
    "implementation": {
      "table_slug": "sku_map",
      "query": { "filter": { "sku": { "op": "eq", "value": "{{sku}}" } }, "limit": 1 }
    },
    "params_schema": {
      "type": "object",
      "properties": { "sku": { "type": "string" } },
      "required": ["sku"]
    }
  }
]
```

## Bind to project

```text
PATCH module cabinet_ids → include cabinet
POST /admin/modules/{id}/projects/{project_id}
```

Only that project's agent context sees rows (MP + scope).
