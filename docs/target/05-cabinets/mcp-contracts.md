# Cabinet MCP contracts

Два контура:

1. **Platform tools `cabinet.*`** — Runtime Prodavan (этот документ).  
2. **User MCP packages** — код агента, install только через deploy — [mcp-packages.md](mcp-packages.md).

## Правила platform tools

1. Имена стабильны (`cabinet.*`).  
2. Аргументы — JSON Schema; строгая валидация.  
3. Нет raw SQL от модели в chat.  
4. Мутации meta → audit.  
5. Квоты до DDL / deploy.  
6. Scoped token = один `cabinet_id` / schema.

## Platform tool set (Base)

### Tables / columns / tabs / views / rows

| Tool | Effect |
|------|--------|
| `cabinet.tables.list/create/update/archive` | Meta + controlled DDL |
| `cabinet.columns.add/update/archive` | Evolve schema |
| `cabinet.tabs.*` / `cabinet.views.*` | Dynamic UI meta |
| `cabinet.rows.query/upsert/delete` | Data plane |

### Declarative mini-tools (без своего кода)

| Tool | Effect |
|------|--------|
| `cabinet.mcp_tools.register` | Wrapper: `rows_upsert` / `rows_query` / `composite` |
| `cabinet.mcp_tools.list/disable` | |

### Packages (custom MCP)

| Tool | Effect |
|------|--------|
| `cabinet.mcp_packages.deploy` | Zip → validate → registry ([mcp-packages](mcp-packages.md)) |
| `cabinet.mcp_packages.list/disable/export` | |

### Bundle / info

| Tool | Effect |
|------|--------|
| `cabinet.bundle.export/import` | Portable cabinet (+ packages) |
| `cabinet.info` | name, versions, quotas |

## Пример: вкладка + package

```text
cabinet.tables.create({ slug: "suppliers", … })
cabinet.views.upsert(…); cabinet.tabs.create({ title: "Поставщики", … })
# агент пишет src/ + manifest + mcp.json, зипует
cabinet.mcp_packages.deploy({ zip: <blob> })
```

## Materialize

1. Inject platform `cabinet.*`.  
2. Start enabled **packages** in sandbox.  
3. Declarative `mcp_tools` as gateway routes.
