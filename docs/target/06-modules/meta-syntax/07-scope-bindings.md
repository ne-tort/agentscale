# Scope, bindings, enabled

Как meta взаимодействует с **module↔cabinet↔project** bindings и UI state (disabled items, project filter).

## Binding model (recap)

```text
Module ──MC──► Cabinet     (install + data schema)
Module ──MP──► Project     (visibility within cabinet; precondition: MC exists)
```

Meta template **shared**; data **per cabinet**; visibility **may filter by project**.

## Scope block

Reusable on `TableDefinition`, `ViewDefinition`, `TabDefinition`, `ActionDefinition`, `MaterializeRule`:

```json
{
  "scope": {
    "projects": "all",
    "requires_assignment": true,
    "module_binding": "required"
  }
}
```

| Field | Values | Meaning |
|-------|--------|---------|
| `projects` | `all` | Visible in cabinet shell always |
| | `bound` | Only when current project has MP row |
| | `none` | Cabinet-level only; hidden in project workspace overlay |
| `requires_assignment` | bool | Employee must have cabinet assignment (default true) |
| `module_binding` | `required` | Implicit — skip if module not bound |

## enabled and visibility

| Field | Effect |
|-------|--------|
| `enabled: false` | Entity exists but UI shows disabled; MCP returns 403 on write |
| `visibility: hidden` | Not in nav; direct access 404 |
| `visibility: disabled` | Shown grayed (tabs) |

**Use cases:**

| Case | Meta |
|------|------|
| Seasonal tab off | `enabled: false` |
| Admin-only table | `scope.projects=none` + company.admin check in policy |
| Beta feature | `visibility: hidden` until flag |

## Condition (enabled_when)

Simple JSON logic v1 — no arbitrary expressions:

```json
{
  "enabled_when": {
    "all": [
      { "field": "status", "op": "eq", "value": "active" },
      { "context.project.status", "op": "neq", "value": "paused" }
    ]
  }
}
```

| op | Types |
|----|-------|
| `eq`, `neq` | any |
| `in` | array |
| `empty`, `not_empty` | string/array |
| `gt`, `lt` | number, datetime |

Context paths:

| Path | Source |
|------|--------|
| `field.*` | Current row body |
| `context.project.*` | Active project |
| `context.cabinet.*` | Cabinet registry |
| `context.user.*` | Employee roles |

v2: CEL or JSONLogic subset — not v1.

## Project binding interaction

```text
Employee opens Cabinet (no project)
  → tabs where scope.projects in (all, none)

Employee opens Project P in Cabinet C
  → tabs scope.projects = all
  → + tabs scope.projects = bound IF MP(module, P) exists
```

Module meta **does not** store project ids at template level — only `scope.projects=bound`; runtime checks `module_project_bindings`.

**Row-level scoping (v1):** column `project_ids` (`type: json`, widget `project_multiselect`) in row body. Empty list = all projects in cabinet. Materialize and future project-context UI filter by this field. Orthogonal to MP: MP = module visible to project; `project_ids` = row included in that project's workspace.

## Multi-module merge

Two modules bind same cabinet:

| Concern | Rule |
|---------|------|
| Tab title collision | Prefix module name |
| Table slug collision | **Forbidden** across modules in one cabinet — validate on bind |
| View slug collision | Namespace: `{module_short}_{view_slug}` internal |

On bind: platform validates `table.slug` unique per cabinet installation set.

## Company / Admin overrides

| Actor | Can |
|-------|-----|
| Platform Admin | Edit module meta template |
| Company admin | Assign employees; cannot edit platform module meta |
| Employee | CRUD data rows; MCP meta mutate if policy allows |

Platform-owned cabinet (`owner_scope=platform`): data read per grant; meta write admin-only.

## Audit fields

Tables with `audit.created_by: true` → `module_data_rows.created_by` populated (already in API).

Meta mutations → `cabinet.meta.audit` (future centralized log).

Дальше: [mcp-tools](08-mcp-tools.md)
