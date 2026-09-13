# Actions — declarative runtime

Actions описывают **функциональную логику** без imperative кода в meta: materialize file, sync, export, webhook (future).

Хранятся в slug `actions` как массив `ActionDefinition`.

## ActionDefinition

```json
{
  "id": "copy_spec_to_project",
  "label": "В проект",
  "description": "Копировать файл спецификации в workspace проекта",
  "kind": "materialize.file",
  "enabled": true,
  "scope": { "projects": "bound" },
  "params": {
    "source": {
      "table_slug": "suppliers",
      "column": "spec_file",
      "from": "row"
    },
    "target": {
      "workspace_path": "inbox/{project.workspace_key}/{filename}",
      "overwrite": false
    }
  },
  "ui": {
    "placement": ["row_action", "toolbar"],
    "icon": "file_copy_outlined",
    "confirm": false
  },
  "permissions": {
    "roles": ["employee", "company.admin"],
    "require_project_active": true
  }
}
```

## Action kinds (allowlist v1)

| kind | Runtime | Params summary |
|------|---------|----------------|
| `data.create_row` | POST data row | `table_slug`, optional `defaults` |
| `data.delete_row` | DELETE row | `table_slug` |
| `data.select_row` | Clear siblings + set selected | `table_slug`, `select_field`, `group_by`, optional `parent` |
| `content.index_tabular` | Parse csv/xlsx → SQLite artifact FileRef | `table_slug`, `source_column`, metadata columns |
| `content.index_opensearch` | Enqueue OpenSearch wipe+bulk (Celery); local header probe sync | `table_slug`, `file_column`, `map_field`, `os_namespace` |
| `materialize.file` | MinIO get → put workspace prefix | `source`, `target` |
| `materialize.rows` | Export rows JSON/CSV to workspace | `table_slug`, `filter`, `target` |
| `materialize.template` | Render template field → file | `source_row`, `field`, `target_path` |
| `storage.upload` | Presigned upload → file_ref column | `column`, `accept` |
| `project.ensure_container` | Trigger Pod ensure (async) | `project_id` from context |
| `mcp.invoke` | Call registered declarative tool | `tool_id`, `args` |
| `http.webhook` | Outbound POST (future, policy-gated) | `url`, `body_template` |

### `data.select_row`

```json
{
  "id": "select_offer_primary",
  "kind": "data.select_row",
  "params": {
    "table_slug": "found_offers",
    "select_field": "is_selected",
    "group_by": "line_id",
    "parent": {
      "table_slug": "request_lines",
      "id_from": "line_id",
      "set_field": "selected_offer_id"
    }
  }
}
```

Semantics: for all rows with the same `group_by` value as the target row, set `select_field=false`; set target `true`; optionally write target id into parent row field.

### `content.index_tabular`

```json
{
  "id": "index_catalog_file",
  "kind": "content.index_tabular",
  "params": {
    "table_slug": "catalogs",
    "source_column": "source_file",
    "artifact_column": "artifact_ref",
    "status_column": "status",
    "row_count_column": "row_count",
    "columns_json_column": "columns_json",
    "error_column": "error",
    "sheet": 0
  },
  "trigger": { "on": ["row.created", "row.updated"], "async": true }
}
```

Accepts csv / xlsx (first sheet). Writes SQLite blob (`CREATE TABLE rows (...);`) as Content asset FileRef. Updates metadata columns. Status enum typically `draft|indexing|ready|error`.

### `content.index_opensearch`

```json
{
  "id": "index_catalog_opensearch",
  "kind": "content.index_opensearch",
  "params": {
    "table_slug": "catalogs",
    "source_kind_column": "source_kind",
    "file_column": "source_file",
    "map_field": "column_map",
    "status_column": "status",
    "error_column": "error",
    "columns_json_column": "columns_json",
    "os_namespace": "equipment"
  },
  "trigger": { "on": ["row.created", "row.updated"], "async": true }
}
```

Sets `status=indexing`, enqueues Celery `prodavan.jobs.index_equipment_catalog` (inline fallback when Celery disabled). Job **deletes** physical index `equipment__c_{row_id}` then bulk-indexes mapped docs. Local: sync header probe into `columns_json` before enqueue. Requires `column_map` with `title` + `price`.

## Triggers (when action runs)

Actions invoked from UI (`ui.placement`) or from events:

```json
{
  "id": "on_project_created_seed",
  "kind": "materialize.rows",
  "trigger": {
    "on": ["project.created", "project.resumed"],
    "async": true
  },
  "params": { "…" }
}
```

| Event | Source |
|-------|--------|
| `project.created` | Platform event |
| `project.resumed` | Platform event |
| `project.paused` | Optional cleanup |
| `row.created` | After data POST |
| `row.updated` | After PATCH |
| `manual` | UI button only |

Platform **materialize job** subscribes to events and runs matching actions.

## UI placement

| placement | Where rendered |
|-----------|----------------|
| `toolbar` | AppEntityCollection toolbar |
| `row_action` | Swipe / overflow menu on row |
| `primary_action` | Alternative to `create_row` in view |
| `fab` | Floating action (narrow only, sparingly) |

## Path templates

| Token | Resolves to |
|-------|-------------|
| `{project.id}` | Current project id |
| `{project.workspace_key}` | MinIO prefix key |
| `{cabinet.id}` | Cabinet id |
| `{module.id}` | Module id |
| `{row.id}` | row_id |
| `{filename}` | From file_ref |
| `{table_slug}` | Table slug |

## Idempotency

| kind | Rule |
|------|------|
| `materialize.file` | Same source key + target path → skip if exists and `overwrite=false` |
| `materialize.rows` | Hash filter+table → marker file `.materialized` in workspace |

## Error handling

| Failure | User | Audit |
|---------|------|-------|
| Missing file_ref | Toast + row badge error | `action.failed` |
| Pod not running | Queue for resume | `action.deferred` |
| Permission denied | 403 | `action.denied` |

## Relation to views

View references action by id:

```json
"row_actions": [{ "kind": "invoke_action", "action": "copy_spec_to_project" }]
```

Interpreter resolves `ActionDefinition`, checks placement + permissions, calls backend `POST /cabinets/.../actions/{id}/run`.

Дальше: [materialize-workspace](06-materialize-workspace.md)
