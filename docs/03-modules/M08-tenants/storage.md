# M08 — Storage (tenant filesystem)

## Root layout

```text
tenants/{tenant_id}/
  tenant.json                 # metadata mirror
  cabinets/{cabinet_id}/
    cabinet.json
    catalogs/                 # M04 — per cabinet only
    integrations/             # M05
    mcp/                      # M06 custom bundles
    projects/{slug}/          # M07
```

**Запрещено:**

```text
/catalogs/                    # global — НЕ существует для боевых данных
/runs/                        # global legacy Commerce
/shared/
```

## Provisioning

On `POST /tenants`:

1. Insert DB rows
2. Create `tenants/{id}/tenant.json`
3. Create default cabinet + `cabinets/{cid}/`
4. Seed `integrations.cabinet_integration_policies` (M05)
5. Optional: copy template `AGENTS.md` to new projects only

## tenant.json

```json
{
  "tenant_id": "uuid",
  "slug": "acme-corp",
  "display_name": "ACME Corp",
  "created_at": "2026-08-20T10:00:00Z"
}
```

## Isolation enforcement

`StorageService.resolve(path)`:

1. Parse tenant_id from JWT
2. Reject if path doesn't start with `tenants/{tenant_id}/`
3. For cabinet-scoped ops, also match `cabinets/{cabinet_id}/`

## Backup per tenant

```text
backup/{tenant_id}/{timestamp}/
  db.dump                     # tenant-scoped pg_dump
  storage.tar.zst             # tenants/{id}/ tree
  manifest.json
```

Restore never merges two tenants.

## Hard delete job

After grace period:

1. List all prefixes `tenants/{id}/`
2. Delete object storage recursively
3. CASCADE DB (all schemas)
4. Audit: `tenant.hard_deleted`

## Quotas by plan

| plan | cabinets | storage | users |
| --- | --- | --- | --- |
| starter | 1 | 5 GB | 3 |
| business | 5 | 50 GB | 20 |
| enterprise | unlimited | custom | custom |

## Platform read-only assets

Separate from tenant data:

```text
/opt/prodavan/platform/
  AGENTS.master.md
  profiles/
  mcp/server_definitions/
```

Copied into project on create, not symlinked (snapshot immutability).
