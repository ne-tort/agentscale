# Module — backend (канon)

Platform Admin CRUD + meta documents + cabinet/project bindings.  
Meta = **shared template**; per-cabinet **data** in `cab_inst_*.module_data_rows`.

## Admin API (`/admin/modules`)

| Method | Route | Body |
|--------|-------|------|
| GET | `` | list |
| POST | `` | `{name}` |
| GET/PATCH/DELETE | `/{module_id}` | patch: `{name?, cabinet_ids?}` |
| GET | `/{module_id}/meta/documents` | list slugs |
| GET/PUT/DELETE | `/{module_id}/meta/documents/{slug}` | JSON body |
| POST/DELETE | `/{module_id}/projects/{project_id}` | bind/unbind (API only) |

## Runtime (employee)

| Method | Route |
|--------|-------|
| GET | `/cabinets/{cabinet_id}/modules` |
| GET | `/cabinets/{cabinet_id}/modules/{module_id}/meta/documents/{slug}` |
| GET/POST/PATCH/DELETE | `/cabinets/{cabinet_id}/modules/{module_id}/data/{table_slug}[/{row_id}]` |

## Services

- `ModuleService` — lifecycle, `_public_row` with bindings
- `ModuleBindingService` — replace cabinet bindings; project bind/revoke; triggers materialize
- `ModuleMetaDocumentService` — slug+JSONB CRUD (platform table, admin only)
- `ModuleMaterializeService` — install/uninstall module in cabinet PG schema
- `CabinetModuleService` — runtime list modules, read template, CRUD data rows

## Reuse model

```text
module_meta_documents (platform)     ← one template, N cabinets
        │
        ├── cab_inst_A.module_data_rows  ← cabinet A data
        └── cab_inst_B.module_data_rows  ← cabinet B data (same module, different rows)
```

## Delete semantics

```text
DELETE module → uninstall data from all cabinets → module row + meta + bindings
              → cabinet_instances and projects unchanged

Unbind cabinet → delete that cabinet's module_data_rows for module_id
```

## Future

- Physical DDL from `tables`/`columns` meta (`storage_kind=physical`)
- Meta syntax validators + Flutter/backend interpreters — см. [meta-syntax](meta-syntax/README.md)
- Company/Employee UI for module visibility
- Propagate template updates to installed cabinets (policy TBD)
