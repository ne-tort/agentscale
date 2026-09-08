# Scope, bindings, enabled

Как meta взаимодействует с **module bind cascade** (local / global) и UI state.

## Binding model

```text
Template → platform instance
Module ──grant──► Company   (local = company instance; global = platform SoT)
Module ──MC────► Cabinet    (local = cabinet fork; global = parent SoT)
Module ──MP────► Project    (local = project leaf; global = parent SoT)
```

Каждая связь: `bind_kind` ∈ {`local`,`global`}, `child_may_edit` bool.

**Канон:** editable path = **SoT instance** from `resolve_sot_instance`.  
**Удалено:** tab `instance_owner`, «пустой MP = все проекты», `project_module_bindings` enable-switch.

| Bind | Child instance | Materialize / UI SoT |
|------|----------------|----------------------|
| local | fork created | child instance |
| global | none | resolve parent |

Product defaults: prompts/MCP/files → `default_project_bind: global`; equipment → `local`.

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

## Project binding interaction

```text
Employee opens Cabinet (no project)
  → tabs where scope.projects in (all, none)
  → management modules with global project binds edit cabinet SoT

Employee opens Project P
  → module appears in materialize iff MP(module, P) exists
  → local MP → project leaf UI; global MP → cabinet (or higher) SoT UI
```

**Row-level scoping:** column `project_ids` (`project_multiselect`). Choices = **module-bound projects only**. Empty list = all bound projects. Orthogonal to bind_kind: MP = module linked to project; `project_ids` = which linked projects receive this row’s artifacts.

## Multi-module merge

Two modules bind same cabinet:

| Concern | Rule |
|---------|------|
| Tab title collision | Prefix module name |
| Table slug collision | **Forbidden** across modules in one cabinet — validate on bind |
| View slug collision | Namespace: `{module_short}_{view_slug}` internal |
