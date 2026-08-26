# MCP tools — declarative layer

Два уровня tools ([mcp-contracts](../../05-cabinets/mcp-contracts.md)):

1. **Platform `cabinet.*`** — always available in Pod  
2. **Declarative `mcp_tools` meta** — wrappers without custom code  
3. **User MCP packages** — zip deploy (out of meta syntax; see [mcp-packages](../../05-cabinets/mcp-packages.md))

Slug: `mcp_tools` — массив `McpToolDefinition`.

## McpToolDefinition

```json
{
  "id": "suppliers_search",
  "name": "suppliers_search",
  "label": "Поиск поставщика",
  "description": "Query suppliers table by name prefix",
  "enabled": true,
  "kind": "rows_query",
  "params_schema": {
    "type": "object",
    "properties": {
      "prefix": { "type": "string", "minLength": 1 }
    },
    "required": ["prefix"]
  },
  "implementation": {
    "table_slug": "suppliers",
    "query": {
      "filter": { "name": { "op": "starts_with", "value": "{{prefix}}" } },
      "limit": 20
    }
  },
  "scope": { "projects": "bound" }
}
```

## Declarative kinds

| kind | Maps to |
|------|---------|
| `rows_query` | `cabinet.rows.query` with fixed table |
| `rows_upsert` | `cabinet.rows.upsert` with column subset |
| `rows_delete` | delete by filter |
| `composite` | Chain of steps (v1.1) |
| `action_ref` | Delegates to ActionDefinition |

## Agent discovery

Pod `mcp.json` materialize includes:

```json
{
  "tools": [
    { "name": "cabinet.rows.query", "…": "platform" },
    { "name": "suppliers_search", "source": "declarative", "module_id": "mod_…" }
  ]
}
```

UI **Tools** system tab lists enabled tools from all bound modules.

## Platform cabinet.* mapping

| Meta operation | Platform MCP |
|----------------|--------------|
| Create table | `cabinet.tables.create` |
| Add column | `cabinet.columns.add` |
| Create tab/view | `cabinet.tabs.create`, `cabinet.views.upsert` |
| CRUD rows | `cabinet.rows.query/upsert/delete` |
| Deploy package | `cabinet.mcp_packages.deploy` |

Meta syntax **describes intent**; agent may use MCP to **mutate meta slugs** (if policy allows) or HTTP admin API.

## Security

| Rule | |
|------|---|
| Tool scoped to cabinet_id in token | |
| Declarative query max `limit` 200 | |
| No arbitrary SQL in `implementation` | |
| `http` kind external calls — Admin policy only |

Дальше: [validation-rules](09-validation-rules.md)
