# M04 — Persistence: каталоги

## catalog_registry (PostgreSQL)

```sql
CREATE TABLE user_catalogs (
  id              TEXT PRIMARY KEY,
  tenant_id       TEXT NOT NULL,
  cabinet_id      UUID NOT NULL REFERENCES cabinets(id),
  slug            TEXT NOT NULL,
  display_name    TEXT NOT NULL,
  format          TEXT NOT NULL,
  status          TEXT NOT NULL,
  trusted_seller  BOOLEAN NOT NULL DEFAULT FALSE,
  storage_path    TEXT NOT NULL,
  schema_hint     JSONB,
  stats           JSONB NOT NULL DEFAULT '{}',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (cabinet_id, slug)
);

CREATE INDEX idx_user_catalogs_cab ON user_catalogs (cabinet_id, status);
```

## system_databases (static seed)

```sql
CREATE TABLE system_databases (
  id              TEXT PRIMARY KEY,
  display_name    TEXT NOT NULL,
  db_type         TEXT NOT NULL,
  deletable       BOOLEAN NOT NULL DEFAULT FALSE,
  requires_capability TEXT,
  requires_profile TEXT,
  virtual         BOOLEAN NOT NULL DEFAULT FALSE
);

INSERT INTO system_databases (id, display_name, db_type, deletable, requires_capability, requires_profile, virtual)
VALUES (
  's4b-cache',
  'S4B API Cache',
  's4b_api_cache',
  FALSE,
  'integrations.s4b',
  'electronics-procurement',
  TRUE
);
```

Trigger prevent delete:

```sql
CREATE OR REPLACE FUNCTION prevent_system_db_delete()
RETURNS TRIGGER AS $$
BEGIN
  IF OLD.deletable IS FALSE THEN
    RAISE EXCEPTION 'SYSTEM_DATABASE_NON_DELETABLE';
  END IF;
  RETURN OLD;
END;
$$ LANGUAGE plpgsql;
```

## s4b_credential_status (tenant)

```sql
CREATE TABLE s4b_credential_status (
  tenant_id       TEXT PRIMARY KEY,
  state           TEXT NOT NULL,
  last_validated_at TIMESTAMPTZ,
  last_error      TEXT,
  rate_limit_reset_at TIMESTAMPTZ,
  username_hint   TEXT,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## credential_vault

Secrets in dedicated vault store (not PG plain text):

| Backend | Key format |
| --- | --- |
| HashiCorp Vault | `secret/prodavan/s4b/{tenant_id}` |
| AWS Secrets Manager | `prodavan/s4b/{tenant_id}` |
| Dev local | encrypted blob in `vault/s4b/{tenant_id}.enc` |

PG holds only `s4b_credential_status`, never password.

### Vault payload schema

```json
{
  "username": "buyer@acme.ru",
  "password_encrypted": "...",
  "updated_at": "2026-08-20T08:00:00Z",
  "key_version": 1
}
```

## s4b_cache_entries (optional materialized cache)

```sql
CREATE TABLE s4b_cache_entries (
  tenant_id       TEXT NOT NULL,
  part_number     TEXT NOT NULL,
  payload         JSONB NOT NULL,
  fetched_at      TIMESTAMPTZ NOT NULL,
  in_stock        BOOLEAN NOT NULL,
  PRIMARY KEY (tenant_id, part_number)
);

CREATE INDEX idx_s4b_cache_fetched ON s4b_cache_entries (tenant_id, fetched_at DESC);
```

TTL eviction job: default 7 days.

## Index jobs

```sql
CREATE TABLE catalog_index_jobs (
  job_id UUID PRIMARY KEY,
  catalog_id TEXT REFERENCES user_catalogs(id),
  status TEXT,
  error JSONB,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ
);
```
