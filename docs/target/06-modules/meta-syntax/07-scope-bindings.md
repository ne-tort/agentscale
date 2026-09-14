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
    "chats": "all",
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
| `chats` | `all` | **Default.** Shared rows for all chats in the SoT instance; `session_id` ignored |
| | `current` | Per-chat rows: list/create require active agent session; filter/stamp `session_id` |
| `requires_assignment` | bool | Employee must have cabinet assignment (default true) |
| `module_binding` | `required` | Implicit — skip if module not bound |

**`scope.chats` is orthogonal to `bind_kind`.** Local/global chooses which **instance** is SoT; `chats` filters **rows inside** that instance. Do **not** fork a module instance per chat.

```text
bind_kind local|global  → cabinet vs project (or higher) instance
scope.chats all|current → shared rows vs session_id-scoped rows
```

| `scope.chats` | List | Create / update |
|---------------|------|-----------------|
| `all` | All rows of the table | Do not stamp `session_id` |
| `current` | Rows where `session_id == activeSession` | Require session; stamp column + `body.session_id`; foreign session → 404/403 |

System field **`session_id`** (agent session id): first-class column on `module_instance_data_rows` + mirrored in JSON body on write. Not shown on ordinary forms.

Wire: UI / MCP send `X-Prodavan-Session-Id` (or query). Pod MCP tools pass `session_id` per call (one Pod serves many chats).

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

**Row-level scoping:** column `project_ids` (`project_multiselect`). Choices = **alive module-bound projects only** (soft-deleted excluded). Empty list = «Все» (all bound). UI lists an explicit «Все» option; picking concrete projects clears it. Orthogonal to bind_kind: MP = module linked to project; `project_ids` = which linked projects receive this row’s artifacts.

## Multi-module merge

Two modules bind same cabinet:

| Concern | Rule |
|---------|------|
| Tab title collision | Prefix module name |
| Table slug collision | **Forbidden** across modules in one cabinet — validate on bind |
| View slug collision | Namespace: `{module_short}_{view_slug}` internal |
