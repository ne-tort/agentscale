# Example: Suppliers module

Полный сценарий: **UI tab + data table + optional file + MCP search**.

## Module intent

| | |
|--|--|
| Name | Suppliers Pack |
| Tab | «Поставщики» |
| Data | name, status, region, spec file |
| Agent | search by name prefix |

## `tables`

```json
[
  {
    "slug": "suppliers",
    "label": "Поставщики",
    "storage_kind": "json_document",
    "enabled": true,
    "scope": { "projects": "all" }
  }
]
```

## `columns`

```json
[
  {
    "table_slug": "suppliers",
    "name": "name",
    "label": "Имя",
    "type": "text",
    "required": true
  },
  {
    "table_slug": "suppliers",
    "name": "status",
    "label": "Статус",
    "type": "enum",
    "required": true,
    "default": "active",
    "enum": {
      "values": ["active", "blocked"],
      "labels": { "active": "Активен", "blocked": "Заблокирован" }
    },
    "ui": { "tone_map": { "blocked": "danger" } }
  },
  {
    "table_slug": "suppliers",
    "name": "region",
    "label": "Регион",
    "type": "text",
    "required": false
  },
  {
    "table_slug": "suppliers",
    "name": "spec_file",
    "label": "Спецификация",
    "type": "file_ref",
    "required": false,
    "file": {
      "accept": ["application/pdf"],
      "max_bytes": 20971520,
      "materialize": { "enabled": true, "target_template": "cabinet-seed/suppliers/{row_id}/{filename}" }
    }
  }
]
```

## `views`

```json
[
  {
    "slug": "suppliers_list",
    "table_slug": "suppliers",
    "kind": "collection",
    "ui_json": {
      "version": 1,
      "kind": "collection",
      "title_field": "name",
      "subtitle_fields": ["status", "region"],
      "columns": [
        { "field": "name", "label": "Имя" },
        { "field": "status", "label": "Статус", "tone_from": "status" },
        { "field": "region", "label": "Регион" }
      ],
      "primary_action": { "kind": "create_row" },
      "row_tap": { "kind": "open_form", "view": "suppliers_form" },
      "empty": { "title": "Нет поставщиков", "action": { "kind": "create_row", "label": "Добавить" } }
    }
  },
  {
    "slug": "suppliers_form",
    "table_slug": "suppliers",
    "kind": "form",
    "ui_json": {
      "version": 1,
      "kind": "form",
      "mode": "edit",
      "fields": [
        { "column": "name", "widget": "value" },
        { "column": "status", "widget": "choice" },
        { "column": "region", "widget": "value" },
        { "column": "spec_file", "widget": "file" }
      ],
      "save": { "kind": "seamless" }
    }
  }
]
```

## `tabs`

```json
[
  {
    "id": "tab_suppliers",
    "title": "Поставщики",
    "order": 100,
    "icon": "local_shipping_outlined",
    "view_slug": "suppliers_list",
    "table_slug": "suppliers",
    "enabled": true,
    "system": false,
    "nav": { "contour": "employee", "placement": "rail" }
  }
]
```

## `materialize` (optional)

```json
[
  {
    "id": "seed_active_suppliers",
    "when": ["project.created", "project.resumed"],
    "source": {
      "type": "rows",
      "table_slug": "suppliers",
      "filter": { "status": "active" }
    },
    "target": {
      "workspace_path": "cabinet-seed/suppliers.json",
      "format": "json_rows"
    }
  }
]
```

## `mcp_tools`

```json
[
  {
    "id": "tool_suppliers_search",
    "name": "suppliers_search",
    "label": "Поиск поставщика",
    "kind": "rows_query",
    "enabled": true,
    "params_schema": {
      "type": "object",
      "properties": { "prefix": { "type": "string" } },
      "required": ["prefix"]
    },
    "implementation": {
      "table_slug": "suppliers",
      "query": {
        "filter": { "name": { "op": "starts_with", "value": "{{prefix}}" } },
        "limit": 20
      }
    }
  }
]
```

## Runtime flow

```text
Admin binds module → 2 cabinets
Cab A employee adds supplier "Alpha"
Cab B employee adds supplier "Beta"
Same template → different module_data_rows
Project create → seed JSON only active suppliers from that cabinet's data
Agent calls suppliers_search(prefix="Al") → rows from scoped cabinet
```
