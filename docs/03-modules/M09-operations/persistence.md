# M09 — Персистентность

Схема: `operations`

## audit_events

```sql
CREATE TABLE operations.audit_events (
  id              UUID NOT NULL DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL,
  cabinet_id      UUID,
  actor_id        UUID,
  actor_type      TEXT NOT NULL,
  event_type      TEXT NOT NULL,
  resource_type   TEXT,
  resource_id     UUID,
  payload         JSONB NOT NULL DEFAULT '{}',
  payload_ref     TEXT,
  ip_address      INET,
  user_agent      TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

CREATE INDEX idx_audit_tenant_time
  ON operations.audit_events (tenant_id, created_at DESC);

CREATE INDEX idx_audit_cabinet_type
  ON operations.audit_events (cabinet_id, event_type, created_at DESC);
```

Monthly partitions: `audit_events_2026_08`.

## token_usage

```sql
CREATE TABLE operations.token_usage (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           UUID NOT NULL,
  cabinet_id          UUID NOT NULL,
  project_id          UUID,
  session_id          UUID,
  run_id              UUID,
  model               TEXT NOT NULL,
  provider            TEXT NOT NULL,
  input_tokens        INT NOT NULL,
  output_tokens       INT NOT NULL,
  cache_read_tokens   INT NOT NULL DEFAULT 0,
  cache_write_tokens  INT NOT NULL DEFAULT 0,
  estimated_cost_usd  NUMERIC(12,6),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_token_usage_tenant_day
  ON operations.token_usage (tenant_id, created_at);

CREATE INDEX idx_token_usage_cabinet
  ON operations.token_usage (cabinet_id, created_at);
```

## token_usage_daily (materialized)

```sql
CREATE MATERIALIZED VIEW operations.token_usage_daily AS
SELECT
  tenant_id,
  cabinet_id,
  date_trunc('day', created_at) AS day,
  model,
  sum(input_tokens) AS input_tokens,
  sum(output_tokens) AS output_tokens,
  sum(estimated_cost_usd) AS cost_usd
FROM operations.token_usage
GROUP BY 1, 2, 3, 4;

CREATE UNIQUE INDEX ON operations.token_usage_daily
  (tenant_id, cabinet_id, day, model);
```

Refresh: hourly cron `REFRESH MATERIALIZED VIEW CONCURRENTLY`.

## retention_jobs

```sql
CREATE TABLE operations.retention_jobs (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_type     TEXT NOT NULL,
  started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at  TIMESTAMPTZ,
  rows_deleted  BIGINT,
  rows_archived BIGINT,
  status        TEXT NOT NULL,
  dry_run       BOOLEAN NOT NULL DEFAULT false
);
```

## RLS

```sql
ALTER TABLE operations.audit_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_audit ON operations.audit_events
  USING (tenant_id = current_setting('app.tenant_id')::uuid);
```

Platform admin uses separate role bypassing RLS with audit of access.

## Emit pipeline

```text
Module → internal/audit/emit → INSERT audit_events
                             → optional Kafka (future)
```

Async buffer optional (Redis list) for burst — flush 1s.

## Partition management

pg_partman or cron:

- Create partition +3 months ahead
- Detach + archive partitions older than retention
- DROP after archive confirm
