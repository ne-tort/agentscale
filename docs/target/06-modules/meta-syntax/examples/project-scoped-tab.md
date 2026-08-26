# Example: Project-scoped tab

Вкладка видна **только** в проектах, где module bound via MP.

## Intent

Checklist per project — not shared across all projects in cabinet.

## Meta highlights

**Tab:**

```json
{
  "id": "tab_checklist",
  "title": "Чеклист",
  "order": 120,
  "view_slug": "checklist_list",
  "table_slug": "checklist_items",
  "scope": { "projects": "bound" },
  "enabled": true,
  "system": false
}
```

**Table:**

```json
{
  "slug": "checklist_items",
  "label": "Checklist",
  "storage_kind": "json_document",
  "scope": { "projects": "bound" }
}
```

**Columns:** `title` text, `done` bool, `project_ref` text (optional explicit project id column for query filter)

## Data isolation pattern

Option A — **single table, filter by project:**

- Column `project_id` required on write (auto-filled from context)
- MCP query always injects `project_id` filter

Option B — **separate row namespaces per project:**

- Same table; rows created only inside project context
- API enforces `body.project_id = current_project` on POST

## UI behavior

| Context | Tab visible? |
|---------|--------------|
| Cabinet home (no project) | No |
| Project P1 with MP binding | Yes |
| Project P2 without MP | No |

## Admin setup

```text
1. Bind module to cabinet (MC)
2. POST /admin/modules/{mod}/projects/{project_id} (MP)
3. Employee opens Project P1 → sees «Чеклист»
```

See [scope-bindings](../07-scope-bindings.md).
