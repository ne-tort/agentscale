# M07 — Персистентность

Схема: `agent`

## Таблицы

### `agent.projects`

```sql
CREATE TABLE agent.projects (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  cabinet_id    UUID NOT NULL REFERENCES tenants.cabinets(id) ON DELETE CASCADE,
  slug          TEXT NOT NULL,
  display_name  TEXT NOT NULL,
  root_path     TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (cabinet_id, slug)
);
```

### `agent.sessions`

```sql
CREATE TABLE agent.sessions (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id            UUID NOT NULL REFERENCES agent.projects(id) ON DELETE CASCADE,
  user_id               UUID NOT NULL REFERENCES tenants.users(id),
  provider_session_id   TEXT,
  model                 TEXT NOT NULL,
  profile_id            TEXT NOT NULL DEFAULT 'kp',
  status                TEXT NOT NULL DEFAULT 'active',
  mcp_snapshot_hash     TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_activity_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_sessions_project_user_active
  ON agent.sessions (project_id, user_id, status)
  WHERE status = 'active';
```

### `agent.messages`

```sql
CREATE TABLE agent.messages (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id      UUID NOT NULL REFERENCES agent.sessions(id) ON DELETE CASCADE,
  role            TEXT NOT NULL,
  content_text    TEXT,
  content_parts   JSONB NOT NULL DEFAULT '[]',
  sequence        BIGINT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (session_id, sequence)
);

CREATE INDEX idx_messages_session_seq ON agent.messages (session_id, sequence);
```

### `agent.runs`

```sql
CREATE TABLE agent.runs (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id            UUID NOT NULL REFERENCES agent.sessions(id),
  trigger_message_id    UUID REFERENCES agent.messages(id),
  status                TEXT NOT NULL,
  model                 TEXT NOT NULL,
  started_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at          TIMESTAMPTZ,
  cancel_requested_at   TIMESTAMPTZ,
  error_code            TEXT,
  token_input           INT NOT NULL DEFAULT 0,
  token_output          INT NOT NULL DEFAULT 0
);
```

### `agent.stream_events`

Partitioned by week. Large payloads → storage reference.

```sql
CREATE TABLE agent.stream_events (
  id          UUID NOT NULL DEFAULT gen_random_uuid(),
  run_id      UUID NOT NULL,
  event_type  TEXT NOT NULL,
  payload     JSONB NOT NULL,
  sequence    BIGINT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id, created_at),
  UNIQUE (run_id, sequence)
) PARTITION BY RANGE (created_at);
```

### `agent.attachments`

```sql
CREATE TABLE agent.attachments (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id          UUID NOT NULL REFERENCES agent.projects(id) ON DELETE CASCADE,
  uploaded_by         UUID NOT NULL REFERENCES tenants.users(id),
  original_filename   TEXT NOT NULL,
  storage_path        TEXT NOT NULL,
  mime_type           TEXT NOT NULL,
  size_bytes          BIGINT NOT NULL,
  extracted_md_path   TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## History retention

| data | retention | policy |
| --- | --- | --- |
| messages | 365 days | M09 job |
| stream_events | 90 days | archive to cold storage optional |
| archived sessions | indefinite | user can delete project |

## Token accounting

On `run.completed`:

```sql
INSERT INTO operations.token_usage (...);  -- M09
```

## Indexes

- Hot path: `(session_id, sequence)` for message pagination
- Stream replay: `(run_id, sequence)`
- Active run lookup: `(session_id, status)` partial where running

## RLS

All tables: `cabinet_id` via join to projects, `SET app.cabinet_id`.
