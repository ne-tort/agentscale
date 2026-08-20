# M06 — Персистентность

Схема: `mcp`

## Таблицы

### `mcp.server_definitions`

Platform-global, seed from migrations.

```sql
CREATE TABLE mcp.server_definitions (
  server_id       TEXT PRIMARY KEY,
  display_name    TEXT NOT NULL,
  version         TEXT NOT NULL,
  transport       TEXT NOT NULL CHECK (transport IN ('stdio', 'sse')),
  entrypoint      TEXT NOT NULL,
  tool_namespace  TEXT NOT NULL,
  category        TEXT NOT NULL,
  trust_level     TEXT NOT NULL,
  manifest_json   JSONB NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### `mcp.installations`

```sql
CREATE TABLE mcp.installations (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  cabinet_id      UUID NOT NULL REFERENCES tenants.cabinets(id) ON DELETE CASCADE,
  server_id       TEXT NOT NULL REFERENCES mcp.server_definitions(server_id),
  state           TEXT NOT NULL DEFAULT 'installed',
  config_json     JSONB NOT NULL DEFAULT '{}',
  error_message   TEXT,
  installed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  installed_by    UUID REFERENCES tenants.users(id),
  enabled_at      TIMESTAMPTZ,
  last_discovery  TIMESTAMPTZ,
  UNIQUE (cabinet_id, server_id)
);

CREATE INDEX idx_mcp_installations_cabinet_state
  ON mcp.installations (cabinet_id, state);
```

### `mcp.tool_catalog`

```sql
CREATE TABLE mcp.tool_catalog (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  installation_id UUID NOT NULL REFERENCES mcp.installations(id) ON DELETE CASCADE,
  tool_name       TEXT NOT NULL,
  fq_name         TEXT NOT NULL,
  description     TEXT,
  input_schema    JSONB NOT NULL,
  discovered_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (installation_id, tool_name)
);

CREATE INDEX idx_tool_catalog_fq ON mcp.tool_catalog (fq_name);
```

### `mcp.profiles`

```sql
CREATE TABLE mcp.profiles (
  profile_id      TEXT NOT NULL,
  tenant_id       UUID REFERENCES tenants.tenants(id) ON DELETE CASCADE,
  display_name    TEXT NOT NULL,
  allowed_servers JSONB NOT NULL,
  tool_allowlist  JSONB,
  tool_denylist   JSONB NOT NULL DEFAULT '[]',
  max_tools       INT,
  is_builtin      BOOLEAN NOT NULL DEFAULT false,
  PRIMARY KEY (profile_id, tenant_id)
);
```

Built-in profiles: `tenant_id IS NULL`, `is_builtin=true`.

### `mcp.session_bindings`

```sql
CREATE TABLE mcp.session_bindings (
  session_id           UUID PRIMARY KEY,
  cabinet_id           UUID NOT NULL,
  profile_id           TEXT NOT NULL,
  snapshot_hash        TEXT NOT NULL,
  effective_tools_json JSONB NOT NULL,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Retention: 30 days (M09 job).

## Кэш Redis

| key | TTL | purpose |
| --- | --- | --- |
| `mcp:discovery:{installation_id}` | 300s | tool list hot |
| `mcp:effective:{cabinet}:{profile}` | 60s | resolved tools |
| `mcp:proc:{installation_id}` | session | pid / health |

## Миграции seed

```sql
INSERT INTO mcp.server_definitions (server_id, ...) VALUES
  ('prodavan-catalog', ...),
  ('prodavan-s4b', ...),
  ('prodavan-offers', ...),
  ('prodavan-pipeline', ...),
  ('prodavan-equipment', ...),
  ('prodavan-integrations', ...),
  ('prodavan-extract', ...);
```

## RLS

Все tenant tables: `cabinet_id` / `tenant_id` isolation как M08.
