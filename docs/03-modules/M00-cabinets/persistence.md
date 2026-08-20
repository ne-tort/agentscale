# M00 — Persistence: кабинеты

СУБД: PostgreSQL 16+. Все таблицы партиционируются логически по `tenant_id` (row-level security).

## ER-диаграмма (упрощённо)

```text
tenants (вне M00)
    │
    ├── cabinets ────────── cabinet_capabilities (JSONB snapshot)
    │       │
    │       └── cabinet_audit_log
    │
    └── cabinet_profiles (глобальный реестр, read-only для runtime)
```

## DDL

### cabinet_profiles

```sql
CREATE TABLE cabinet_profiles (
  id              TEXT PRIMARY KEY,              -- electronics-procurement
  version         TEXT NOT NULL,                 -- semver
  display_name    TEXT NOT NULL,
  description     TEXT,
  capabilities_schema JSONB NOT NULL,
  pack_checksum   TEXT NOT NULL,                 -- sha256 pack tarball
  deprecated      BOOLEAN NOT NULL DEFAULT FALSE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### cabinets

```sql
CREATE TYPE cabinet_status AS ENUM ('provisioning', 'active', 'archived', 'failed');

CREATE TABLE cabinets (
  id              UUID PRIMARY KEY,
  tenant_id       TEXT NOT NULL REFERENCES tenants(id),
  slug            TEXT NOT NULL,
  display_name    TEXT NOT NULL,
  profile_id      TEXT NOT NULL REFERENCES cabinet_profiles(id),
  profile_version TEXT NOT NULL,
  status          cabinet_status NOT NULL DEFAULT 'provisioning',
  capabilities    JSONB NOT NULL,                  -- frozen effective snapshot
  metadata        JSONB NOT NULL DEFAULT '{}',
  created_by      UUID NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  archived_at     TIMESTAMPTZ,
  CONSTRAINT cabinets_slug_unique UNIQUE (tenant_id, slug)
);

CREATE INDEX idx_cabinets_tenant_status ON cabinets (tenant_id, status);
CREATE INDEX idx_cabinets_profile ON cabinets (profile_id);
```

### cabinet_seed_runs

Журнал pack seed pipeline (идемпотентность и отладка).

```sql
CREATE TABLE cabinet_seed_runs (
  id              UUID PRIMARY KEY,
  cabinet_id      UUID NOT NULL REFERENCES cabinets(id) ON DELETE CASCADE,
  pack_version    TEXT NOT NULL,
  step            TEXT NOT NULL,                 -- materialize_storage, seed_prompts, ...
  status          TEXT NOT NULL,                 -- pending, ok, failed
  error_detail    JSONB,
  started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at     TIMESTAMPTZ,
  UNIQUE (cabinet_id, pack_version, step)
);
```

### cabinet_sessions

Привязка активного кабинета к сессии (switch API).

```sql
CREATE TABLE cabinet_sessions (
  session_id      TEXT PRIMARY KEY,
  tenant_id       TEXT NOT NULL,
  user_id         UUID NOT NULL,
  active_cabinet_id UUID REFERENCES cabinets(id),
  switched_at     TIMESTAMPTZ,
  previous_cabinet_id UUID,
  CONSTRAINT fk_active_cabinet FOREIGN KEY (active_cabinet_id, tenant_id)
    -- enforced via trigger: cabinet.tenant_id = session.tenant_id
);

CREATE INDEX idx_cabinet_sessions_user ON cabinet_sessions (user_id, tenant_id);
```

### cabinet_audit_log

```sql
CREATE TABLE cabinet_audit_log (
  id              BIGSERIAL PRIMARY KEY,
  tenant_id       TEXT NOT NULL,
  cabinet_id      UUID,
  actor_user_id   UUID,
  action          TEXT NOT NULL,                 -- create, archive, restore, switch
  payload         JSONB,
  ip_address      INET,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_cabinet_audit_tid_cid ON cabinet_audit_log (tenant_id, cabinet_id, created_at DESC);
```

## Row-Level Security

```sql
ALTER TABLE cabinets ENABLE ROW LEVEL SECURITY;

CREATE POLICY cabinets_tenant_isolation ON cabinets
  USING (tenant_id = current_setting('app.tenant_id', true));
```

Middleware устанавливает `SET app.tenant_id = :tid` на каждое соединение.

## Триггер: запрет s4b вне electronics

```sql
CREATE OR REPLACE FUNCTION enforce_s4b_profile()
RETURNS TRIGGER AS $$
BEGIN
  IF (NEW.capabilities->'integrations'->'s4b'->>'enabled')::boolean IS TRUE
     AND NEW.profile_id <> 'electronics-procurement' THEN
    RAISE EXCEPTION 'CAPABILITY_FORBIDDEN: s4b only for electronics-procurement';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_cabinets_s4b
  BEFORE INSERT OR UPDATE ON cabinets
  FOR EACH ROW EXECUTE FUNCTION enforce_s4b_profile();
```

## Миграции

| Версия | Изменение |
| --- | --- |
| M00_001 | cabinet_profiles, cabinets |
| M00_002 | cabinet_seed_runs |
| M00_003 | cabinet_sessions, audit |
| M00_004 | RLS policies, s4b trigger |

## Кэш (Redis, опционально)

| Key | TTL | Значение |
| --- | --- | --- |
| `cab:cap:{cid}` | 5m | JSON capabilities |
| `cab:switch:{session_id}` | session | active cid |

Инвалидация при `switch`, `archive`, `patch`.

## Резервное копирование

- Таблицы `cabinets`, `cabinet_seed_runs` — ежедневный snapshot.
- Storage кабинета — отдельный бэкап по prefix `storage/cabinets/{tid}/{cid}/` (см. storage.md).
