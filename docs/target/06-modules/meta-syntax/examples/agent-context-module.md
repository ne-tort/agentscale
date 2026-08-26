# Example: Agent context module

Meta-only для **Pod workspace** — минимальный UI, акцент на materialize.

## Intent

| | |
|--|--|
| Purpose | Inject AGENTS.md, prompts, rules into project Pod |
| UI | Optional read-only «Контекст» tab |
| Data | Rows editable in Context system tab or dedicated form |

## `tables`

```json
[
  {
    "slug": "agent_docs",
    "label": "Agent docs",
    "storage_kind": "json_document",
    "scope": { "projects": "all" }
  },
  {
    "slug": "prompts",
    "label": "Prompts",
    "storage_kind": "json_document",
    "scope": { "projects": "all" }
  }
]
```

## `columns` (abbreviated)

```json
[
  { "table_slug": "agent_docs", "name": "slug", "type": "text", "required": true, "unique": true },
  { "table_slug": "agent_docs", "name": "body_md", "type": "text", "required": true },
  { "table_slug": "prompts", "name": "name", "type": "text", "required": true },
  { "table_slug": "prompts", "name": "body_md", "type": "text", "required": true }
]
```

## `materialize`

```json
[
  {
    "id": "agents_md",
    "when": ["project.created", "project.resumed"],
    "priority": 1,
    "source": {
      "type": "row",
      "table_slug": "agent_docs",
      "row_id": "default",
      "field": "body_md"
    },
    "target": { "workspace_path": "AGENTS.md", "format": "raw" }
  },
  {
    "id": "prompts_dir",
    "when": ["project.created", "project.resumed"],
    "priority": 20,
    "source": {
      "type": "rows",
      "table_slug": "prompts",
      "filter": {}
    },
    "target": {
      "workspace_path": "prompts/{row.name}.md",
      "format": "template",
      "template_field": "body_md"
    }
  }
]
```

Note: multi-row template targets — engine expands one file per row.

## `tabs` (optional)

```json
[
  {
    "id": "tab_context_docs",
    "title": "Контекст",
    "order": 110,
    "view_slug": "agent_docs_read",
    "enabled": true,
    "system": false
  }
]
```

## `views`

Read-only form for `agent_docs` row `default`.

## Agent workflow

```text
Employee opens Project chat
  → project.created already ran materialize
  → Pod has fresh AGENTS.md + prompts/*.md
Agent edits via cabinet.rows.upsert on prompts table
  → employee triggers refresh or project.resumed re-materialize
```

No custom MCP package required for basic context.
