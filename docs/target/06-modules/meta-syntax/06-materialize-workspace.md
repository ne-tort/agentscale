# Materialize — workspace & Pod

Правила **что из meta/data попадает в MinIO** и далее в Pod `/workspace`.

Slug: `materialize` — массив `MaterializeRule[]`.  
См. также [materialize-from-meta](../../05-cabinets/materialize-from-meta.md).

## MaterializeRule

```json
{
  "id": "agents_md_default",
  "enabled": true,
  "when": ["project.created", "project.resumed"],
  "priority": 10,
  "source": {
    "type": "row",
    "table_slug": "agent_docs",
    "row_id": "default",
    "field": "body_md"
  },
  "target": {
    "workspace_path": "AGENTS.md",
    "format": "raw"
  }
}
```

| Field | Description |
|-------|-------------|
| `id` | Stable rule id |
| `when` | Event list (same as action triggers) |
| `priority` | Lower runs first |
| `source` | Where to read |
| `target` | Where to write in workspace |
| `condition` | Optional Condition block |

## Source types

| type | Description |
|------|-------------|
| `row` | Single row field |
| `rows` | Query table with filter |
| `file_ref` | Column FileRef → blob copy |
| `meta_document` | Module meta slug (template text) |
| `mcp_package` | Enabled package artifact |
| `static` | Inline string in rule (small prompts) |

### rows + filter example

```json
{
  "source": {
    "type": "rows",
    "table_slug": "suppliers",
    "filter": { "materialize": true, "status": "active" },
    "module_id": "{module.id}"
  },
  "target": {
    "workspace_path": "cabinet-seed/suppliers.json",
    "format": "json_rows"
  }
}
```

## Target formats

| format | Output |
|--------|--------|
| `raw` | Field value as file bytes/text |
| `json_rows` | JSON array of row bodies |
| `json_single` | One row body object |
| `template` | Mustache-style `{{field}}` in template field |
| `copy_blob` | Binary copy from MinIO object_key |

## Workspace layout (канон)

```text
projects/{workspace_key}/
  AGENTS.md
  prompts/
  rules/
  skills/
  mcp.json
  packages/
  cabinet-seed/
  inbox/
  out/
```

Materialize rules **must** target paths under this tree.

## file_ref flow

```text
1. User uploads → MinIO cabinets/{cabinet_id}/files/…
2. Row stores FileRef in module_data_rows.body
3. project.created → materialize rule copy_blob
4. Pod hydrate → /workspace/inbox/spec.pdf
5. Agent reads local file; live table via cabinet.rows MCP
```

## Re-materialize on resume

```text
project.resumed
  → load enabled materialize rules
  → skip unchanged (etag/sha256 match)
  → update changed files only
  → ContainerRuntimePort.start → hydrate
```

## What NOT to materialize

| ✗ | Reason |
|---|--------|
| Entire platform DB dump | Isolation |
| Other cabinet schemas | Isolation |
| AI provider API keys plaintext | Use scoped secret channel |
| Raw module_meta_documents | Agent uses MCP read, not file dump |

## Backend implementation map

| Step | Component |
|------|-----------|
| Resolve rules | `MaterializePlanner` (reads module meta + bindings) |
| Read data | `CabinetModuleService` + MinIO client |
| Write blobs | `WorkspaceLayoutWriter` / object store |
| Start Pod | `ContainerRuntimePort` ([14-project-containers](../../14-project-containers/)) |

## Declarative vs actions

| | `materialize` slug | `actions` slug |
|---|-------------------|----------------|
| Purpose | Bulk sync on lifecycle events | User-triggered or single-row ops |
| Typical when | project.created/resumed | button tap |
| Overlap | Allowed — same engine executes both |

Дальше: [scope-bindings](07-scope-bindings.md)
