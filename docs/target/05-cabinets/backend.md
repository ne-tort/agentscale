# Cabinets — backend (MVP)

## Cabinet registry (platform DB)

| Concern | Implementation |
|---------|----------------|
| Create (Admin) | `POST /admin/cabinets` — `{name, company_id}` → provision schema + row (`owner_employee_id` nullable) |
| Create (Employee) | `POST /cabinets` — requires employee; owner set for audit |
| List / get / rename | Admin CRUD + employee list/get/rename/archive/delete |
| Rebind company | `PATCH /admin/cabinets/{id}` `{company_id}` |
| Delete | Admin: wipe projects (MinIO/Pod) → `DROP SCHEMA` → delete row (**no** archive required). Employee hard-delete still after archive |
| Authz | Platform admin CRUD; employee own/list; Company assignment N:M — **next stage** ([assignment](assignment.md)) |

## Schema per instance

New instances provision **only**:

```sql
CREATE TABLE meta_documents (
  slug TEXT PRIMARY KEY,
  body JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Legacy typed meta DDL / MCP seed / bundles — **removed from MVP**. Existing `cab_inst_*` with old tables may remain until orphan GC.

## Meta layer (not entity core)

HTTP: `GET/PUT/DELETE /cabinets/{id}/meta/documents[/{slug}]`.

Validation: `body` must be a JSON **object or array** only — no field schema.

## Persistence default

- One Postgres cluster.  
- **Schema per CabinetInstance** (`cab_inst_<id>`).  
- Quotas / company binding in platform schema.

## Не делать (MVP)

- Typed meta tables/columns/views/tabs DDL.  
- Bundle import/export / starter catalog as part of cabinet entity.  
- MCP packages as part of cabinet entity.  
- Separate Python package per domain cabinet.
