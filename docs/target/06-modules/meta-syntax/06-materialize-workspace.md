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
| `copy_blob` | Binary copy from Content Service blob (`storage_key` / `version_id`) |
| `merge_mapped_sqlite` | Merge N row SQLite artifacts via per-row `column_map` into one canonical `rows` table |
| `prompt_paths` | Expand `prompt_paths.files_json` → one `raw` file per entry under `path`/`name`.md; skip row if `files_json` empty |

### `prompt_paths`

Cabinet-owned prompts module. Empty path cards do **not** create directories. Root path + `AGENTS.md` → workspace `AGENTS.md` only (no `CLAUDE.md` alias).

```json
{
  "source": {
    "type": "rows",
    "table_slug": "catalogs",
    "filter": { "status": "ready", "paused": false }
  },
  "target": {
    "workspace_path": "catalogs/catalog.sqlite",
    "format": "merge_mapped_sqlite",
    "artifact_field": "artifact_ref",
    "map_field": "column_map",
    "schema": ["title", "price", "part_number", "supplier", "lead_time", "source_catalog"],
    "required_map_keys": ["title", "price"],
    "provenance": { "target": "source_catalog", "from": "name" }
  }
}
```

Rows without a complete required map are skipped. `project_ids` on each body is always applied (empty = all projects). Bool filters treat missing keys as `false`.

## Workspace layout (канон)

```text
projects/{workspace_key}/
  AGENTS.md              # only when a prompt file materializes (not pre-created empty)
  mcp.json
  packages/
  cabinet-seed/
  inbox/
  out/
  # rules/ skills/ prompts/ — created on write when files_json non-empty
```

Materialize rules **must** target paths under this tree.

## file_ref flow (Content Service)

Three phases — see [12-content-file-pipeline](12-content-file-pipeline.md):

```text
Phase A — Upload
  Meta UI file_ref → POST /cabinets/{id}/content/upload
  → MinIO blobs/{uuid} + content_assets row
  → FileRef in module_data_rows.body

Phase B — Reference
  Row holds asset_id, version_id, storage_key (no re-upload)

Phase C — Materialize
  project.created | sync | resumed
  → MaterializeExecutor copy_blob → projects/{workspace_key}/workspace/{path}
  → Pod initContainer hydrate → /workspace/{path}
```

Example `copy_blob` rule:

```json
{
  "target": {
    "workspace_path": "{{target_path}}",
    "format": "copy_blob",
    "field": "file_ref"
  }
}
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
