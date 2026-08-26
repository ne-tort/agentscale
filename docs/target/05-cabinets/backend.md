# Cabinets — backend (MVP)

## Cabinet registry (platform DB)

| Concern | Implementation |
|---------|----------------|
| Create (Admin) | `POST /admin/cabinets` — provision schema + row |
| Create (Employee) | `POST /cabinets` — requires employee |
| List / get / rename / archive / delete | Admin CRUD + employee runtime |
| Authz | Platform admin CRUD; employee via assignment/grants |

## Schema per instance

Provision **module runtime data layer** only (no cabinet-level meta):

```sql
CREATE TABLE module_installations (
  module_id TEXT PRIMARY KEY,
  installed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE module_data_rows (
  module_id TEXT NOT NULL,
  table_slug TEXT NOT NULL,
  row_id TEXT NOT NULL,
  body JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_by TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (module_id, table_slug, row_id)
);
```

Legacy `meta_documents` in existing schemas — orphan until cabinet recreate/GC.

## Modules in cabinet (runtime HTTP)

| Method | Route | Смысл |
|--------|-------|--------|
| GET | `/cabinets/{id}/modules` | bound modules |
| GET | `/cabinets/{id}/modules/{mod}/meta/documents/{slug}` | read shared template |
| GET/POST | `/cabinets/{id}/modules/{mod}/data/{table}` | list/create rows |
| PATCH/DELETE | `…/data/{table}/{row_id}` | update/delete row |

Meta write — Admin only (`/admin/modules/.../meta`). Data write — employee with cabinet access.

## Bind → materialize

On `PATCH /admin/modules/{id}` `{cabinet_ids}`:
- **add** binding → `module_installations` row in cabinet schema
- **remove** binding → delete module data rows + installation row

On module delete → uninstall data from all bound cabinets before CASCADE.

## Persistence

- One Postgres cluster, **schema per CabinetInstance** (`cab_inst_<id>`).
- Shared module templates in platform DB; **data isolation by schema**.

Дальше: [06-modules/backend](../06-modules/backend.md) · [meta-and-ui](meta-and-ui.md).
