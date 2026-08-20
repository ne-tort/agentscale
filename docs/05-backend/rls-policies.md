# Row Level Security (RLS)

PostgreSQL RLS — **основной механизм изоляции** tenant/cabinet данных. Application-level checks (RBAC) дополняют RLS (defense in depth), но **не заменяют** его.

---

## Session variables (GUC)

На каждое DB-соединение middleware выставляет:

```sql
SELECT set_config('app.tenant_id',  :tenant_id,  true);  -- transaction-local
SELECT set_config('app.cabinet_id', :cabinet_id, true);  -- optional, project-scoped
SELECT set_config('app.user_id',     :user_id,    true);
SELECT set_config('app.role',       :role,       true);  -- tenant / cabinet role
```

| GUC | Source | Required |
|-----|--------|----------|
| `app.tenant_id` | JWT claim | always |
| `app.cabinet_id` | JWT or `X-Cabinet-Id` | cabinet/project scoped endpoints |
| `app.user_id` | JWT sub | always |
| `app.role` | computed RBAC | optional, for role-based policies |

Implementation: `infrastructure/persistence/rls.py` → `apply_rls(session, ctx)`.

---

## Policy patterns

### Pattern A: tenant-only

Для таблиц без `cabinet_id` (global within tenant):

```sql
ALTER TABLE tenants.tenant_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants.tenant_memberships FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON tenants.tenant_memberships
  FOR ALL
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
```

### Pattern B: tenant + cabinet

Для большинства business tables:

```sql
ALTER TABLE projects.projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE projects.projects FORCE ROW LEVEL SECURITY;

CREATE POLICY cabinet_isolation ON projects.projects
  FOR ALL
  USING (
    tenant_id = current_setting('app.tenant_id', true)::uuid
    AND cabinet_id = current_setting('app.cabinet_id', true)::uuid
  )
  WITH CHECK (
    tenant_id = current_setting('app.tenant_id', true)::uuid
    AND cabinet_id = current_setting('app.cabinet_id', true)::uuid
  );
```

### Pattern C: tenant-wide read, cabinet write

Для audit (tenant admin видит все cabinets):

```sql
CREATE POLICY audit_tenant_read ON ops.audit_log
  FOR SELECT
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid);

CREATE POLICY audit_cabinet_write ON ops.audit_log
  FOR INSERT
  WITH CHECK (
    tenant_id = current_setting('app.tenant_id', true)::uuid
    AND (
      cabinet_id IS NULL
      OR cabinet_id = nullif(current_setting('app.cabinet_id', true), '')::uuid
    )
  );
```

### Pattern D: platform admin bypass

Отдельная role `prodavan_platform` **BYPASSRLS** — только break-glass service account, не API user.

---

## RLS per table

### Schema `tenants`

| Table | Pattern | Notes |
|-------|---------|-------|
| `tenants` | Platform only | RLS off; access via admin API |
| `users` | Self + tenant member | `id = app.user_id OR EXISTS membership` |
| `cabinets` | A (tenant) | |
| `tenant_memberships` | A | |
| `cabinet_memberships` | B via join | cabinet_id match |
| `invites` | A | |
| `refresh_tokens` | Self | `user_id = app.user_id` |

### Schema `cabinets`

| Table | Pattern |
|-------|---------|
| `cabinet_profiles` | Public read | RLS off — platform catalog |
| `cabinet_capabilities` | B |
| `pack_installs` | B |

### Schema `projects`

| Table | Pattern |
|-------|---------|
| `projects` | B |
| `attachments` | B |

### Schema `specs`

| Table | Pattern |
|-------|---------|
| `spec_runs` | B |
| `line_items` | B |
| `offers` | B |
| `variants` | B |
| `specs` | B |
| `spec_links` | B (via join trigger) |
| `run_artifacts` | B (via run_id join) |

### Schema `prompts`

| Table | Pattern |
|-------|---------|
| `prompt_documents` | B |
| `prompt_versions` | B (via document_id FK policy) |

### Schema `catalogs`

| Table | Pattern |
|-------|---------|
| `catalog_databases` | B |
| `catalog_import_jobs` | B |

### Schema `integrations`

| Table | Pattern |
|-------|---------|
| `cabinet_integration_policies` | B (PK cabinet_id) |
| `web_shop_allowlist` | B |
| `s4b_trusted_sellers` | B |
| `s4b_credentials` | B |
| `integration_call_log` | B |

Пример из M05:

```sql
ALTER TABLE integrations.web_shop_allowlist ENABLE ROW LEVEL SECURITY;
CREATE POLICY cabinet_isolation ON integrations.web_shop_allowlist
  USING (cabinet_id = current_setting('app.cabinet_id', true)::uuid);
```

### Schema `mcp`

| Table | Pattern |
|-------|---------|
| `server_definitions` | Public read |
| `installations` | B |
| `tool_catalog` | Public read |
| `agent_profiles` | Public read |
| `session_bindings` | B (via session join) |

### Schema `agent`

| Table | Pattern |
|-------|---------|
| `sessions` | B |
| `messages` | B (via session) |
| `runs` | B (via session) |
| `stream_events` | B (via run join) |

### Schema `ops`

| Table | Pattern |
|-------|---------|
| `audit_log` | C |
| `metrics_snapshots` | A |

---

## Application behavior

### Missing GUC

Если `app.cabinet_id` не установлен на cabinet-scoped query:

- RLS → **0 rows** (не error)
- Application maps to `NotFoundError` (404) — не leak existence

### Cross-tenant ID guess

JWT tenant A + resource ID from tenant B → 0 rows → **404**

### Same tenant, wrong cabinet

JWT cabinet A + project from cabinet B → 0 rows → **404**

Explicit RBAC violation (same cabinet, insufficient role) → **403**

---

## Worker pod DB access

Worker pods **не** подключаются к PostgreSQL напрямую для business data (MVP).

Исключение: internal metrics sidecar — read-only role с RLS.

MCP Gateway использует service account:

```sql
CREATE ROLE mcp_gateway_service;
GRANT USAGE ON SCHEMA integrations TO mcp_gateway_service;
-- policies include cabinet_id from JWT envelope, not GUC
```

---

## Testing RLS

### Integration tests

```python
async def test_cross_cabinet_blocked(db, tenant_a, cabinet_a, cabinet_b):
    await apply_rls(db, ctx_a)
    project_b = await create_project(cabinet_b)
    await apply_rls(db, ctx_a)  # cabinet_a context
    result = await repo.get(project_b.id)
    assert result is None  # RLS filtered
```

### Negative test catalog

| ID | Scenario |
|----|----------|
| RLS-001 | Tenant A cannot SELECT tenant B cabinets |
| RLS-002 | Cabinet A cannot read cabinet B integrations |
| RLS-003 | Missing app.cabinet_id → empty result |
| RLS-004 | FORCE RLS applies to table owner |
| RLS-005 | SQL injection in GUC → parameterized set_config |

---

## Migration checklist

При добавлении таблицы:

- [ ] `tenant_id UUID NOT NULL` (+ `cabinet_id` if scoped)
- [ ] Index on `(tenant_id, cabinet_id)`
- [ ] `ENABLE ROW LEVEL SECURITY`
- [ ] `FORCE ROW LEVEL SECURITY`
- [ ] CREATE POLICY in dedicated Alembic revision
- [ ] Integration test cross-tenant

---

## Связанные документы

- [alembic.md](alembic.md)
- [erd-v0.md](erd-v0.md)
- [../02-architecture/multi-tenancy.md](../02-architecture/multi-tenancy.md)
- [secrets.md](secrets.md)
