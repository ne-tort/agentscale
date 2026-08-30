# Content & file pipeline — upload, reference, Pod materialize

Три **явно разделённые** фазы работы с файлами в meta-table syntax.  
Не смешивать upload в MinIO, хранение ссылки в строке и копирование в workspace Pod.

См. также [tables-and-columns](02-tables-and-columns.md) · [materialize-workspace](06-materialize-workspace.md) · [15-content-storage](../../15-content-storage/README.md).

## Три фазы

```text
Phase A — Upload (Content Service)
  Meta UI file_ref column → POST /cabinets/{id}/content/upload
  → MinIO blobs/{uuid} + content_assets row
  → FileRef written into module_data_rows.body

Phase B — Reference (row storage only)
  Row body holds canonical FileRef (asset_id, version_id, storage_key)
  No duplicate upload on materialize; ID is source of truth

Phase C — Pod materialize (project workspace)
  materialize rule (copy_blob | raw) on project.created / sync / resumed
  → MinIO projects/{workspace_key}/workspace/{path}
  → Pod initContainer hydrate → /workspace/{path}
```

| Phase | Who triggers | Storage | Pod sees |
|-------|--------------|---------|----------|
| A | User in cabinet meta UI | Content Service + `blobs/` | — |
| B | API row write | `module_data_rows.body` | — |
| C | Launch / sync / resume | Project workspace prefix | Local file |

## Phase A — Upload via Content Service

**Column:** `type: file_ref` + `file` block (accept, max_bytes).

**UI:** `FileUploadField` → `POST /api/v1/cabinets/{cabinet_id}/content/upload`.

**Backend:** `CabinetContentUploadService` → `UploadService.upload_bytes_as_asset()` → MinIO.

**Response → row field** (canonical FileRef, as-built):

```json
{
  "asset_id": "ca_abc123",
  "version_id": "cbv_def456",
  "blob_version_id": "cbv_def456",
  "storage_key": "blobs/abc123def456",
  "filename": "prompt.md",
  "content_type": "text/markdown",
  "size": 1234,
  "sha256": "…"
}
```

Legacy alias `object_key` in docs — **deprecated**; use `storage_key`.

### Column `file` block (upload constraints only)

```json
{
  "table_slug": "files",
  "name": "attachment",
  "type": "file_ref",
  "required": false,
  "file": {
    "accept": ["application/pdf", "text/markdown", ".md"],
    "max_bytes": 20971520
  }
}
```

Optional `file.materialize.target_template` in column meta — **spec only** (not auto-generated in MVP); declare explicit rule in slug `materialize`.

## Phase B — Reference in row

After upload, **only** FileRef lives in row JSON. Materialize reads this object; it does not re-upload.

| Field | Required | Role |
|-------|----------|------|
| `asset_id` | ✓ | Content Service asset identity |
| `version_id` | ✓ | Blob version for ACL + download |
| `storage_key` | ✓ | MinIO key (`blobs/…`) |
| `filename` | ✓ | Display + target basename |
| `content_type` | | MIME |
| `size` | | Bytes |
| `sha256` | | Integrity (future skip-if-unchanged) |

**Validation (target):** on row write, verify asset exists and cabinet ACL allows read — **gap** P-META-FILE-02.

## Phase C — Materialize into Pod workspace

### Binary file (uploaded blob)

```json
{
  "id": "files_to_workspace",
  "enabled": true,
  "when": ["project.created", "project.sync", "project.resumed"],
  "priority": 20,
  "source": {
    "type": "rows",
    "table_slug": "files",
    "filter": { "enabled": true }
  },
  "target": {
    "workspace_path": "{{target_path}}",
    "format": "copy_blob",
    "field": "file_ref"
  }
}
```

Executor: `MaterializeExecutor._write_copy_blob` resolves blob via `storage_key` or `version_id` → writes under `projects/{workspace_key}/workspace/`.

### Text / Markdown from column (no upload)

Create `.md` files from text fields — **no Content Service upload**:

```json
{
  "id": "prompt_to_md",
  "enabled": true,
  "when": ["project.created", "project.sync"],
  "source": {
    "type": "row",
    "table_slug": "prompts",
    "row_id": "{active_profile_id}",
    "field": "body_md"
  },
  "target": {
    "workspace_path": "prompts/{{name}}.md",
    "format": "raw"
  }
}
```

Column setup: `type: text` + `ui.widget: markdown_editor` in view (see [views-ui](03-views-ui.md)).

**Gap:** `format: template` (Mustache in path/body) — documented, not in executor yet (P-META-FILE-03).

## End-to-end diagram

```text
┌─────────────┐     upload      ┌──────────────────┐
│ Meta UI     │ ──────────────► │ Content Service  │
│ file_ref    │                 │ → MinIO blobs/   │
└─────────────┘                 └────────┬─────────┘
                                         │ FileRef
                                         ▼
                                ┌──────────────────┐
                                │ module_data_rows │
                                └────────┬─────────┘
                                         │ launch/sync
                                         ▼
                                ┌──────────────────┐
                                │ MaterializeExecutor
                                │ copy_blob | raw  │
                                └────────┬─────────┘
                                         ▼
                                projects/{ws}/workspace/
                                         │ hydrate
                                         ▼
                                Pod /workspace/…
```

## Product modules (reference)

| Module | Pattern | Seeds |
|--------|---------|-------|
| Prompts | `raw` → AGENTS.md, rules/, skills/ | `mod_prompts` |
| Files | `copy_blob` by `target_path` | `mod_files` |
| MCP | `mcp_package` zip extract | `mod_mcp` |

## Gaps (implementation backlog)

| ID | Gap |
|----|-----|
| P-META-FILE-01 | Align all docs/examples from `object_key` → canonical FileRef |
| P-META-FILE-02 | Row write validates FileRef against Content Service |
| P-META-FILE-03 | `format: template` in MaterializeExecutor |
| P-META-FILE-04 | Auto materialize rule from column `file.materialize` |
| P-META-FILE-05 | Re-materialize skip by sha256/etag |
