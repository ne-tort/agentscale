# M01 — Persistence: проекты

## project.json (source of truth на диске)

```json
{
  "id": "proj_7f3a9c2e",
  "cabinet_id": "0195a1b2-c3d4-7890-abcd-ef1234567890",
  "tenant_id": "acme-corp",
  "slug": "client-alpha",
  "display_name": "Клиент Alpha",
  "workspace_key": "cab:acme-corp:0195a1b2-c3d4-7890-abcd-ef1234567890:proj_7f3a9c2e",
  "status": "active",
  "schema_version": 1,
  "created_at": "2026-08-20T07:00:00Z",
  "updated_at": "2026-08-20T07:00:00Z"
}
```

## PostgreSQL mirror (optional, для list/search)

```sql
CREATE TABLE projects (
  id              TEXT PRIMARY KEY,
  tenant_id       TEXT NOT NULL,
  cabinet_id      UUID NOT NULL REFERENCES cabinets(id),
  slug            TEXT NOT NULL,
  display_name    TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'active',
  workspace_key   TEXT NOT NULL UNIQUE,
  last_opened_at  TIMESTAMPTZ,
  created_by      UUID NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  archived_at     TIMESTAMPTZ,
  UNIQUE (cabinet_id, slug)
);

CREATE INDEX idx_projects_cabinet ON projects (cabinet_id, status);
CREATE INDEX idx_projects_workspace ON projects (workspace_key);
```

## project_sessions

```sql
CREATE TABLE project_sessions (
  session_id      TEXT PRIMARY KEY,
  tenant_id       TEXT NOT NULL,
  cabinet_id      UUID NOT NULL,
  active_project_id TEXT REFERENCES projects(id),
  opened_at       TIMESTAMPTZ
);
```

## commerce.sqlite (per project)

Локальная SQLite для M02 variants — не в PG.

Основные таблицы (управляет M02/MCP commerce-offers):

| Таблица | Назначение |
| --- | --- |
| `line_items` | Позиции спеки |
| `offers` | Варианты офферов |
| `offer_scores` | Оценки rank |
| `equipment_cards` | Карточки техники (electronics) |

Path: `{project_root}/commerce.sqlite`

## Sync policy

- Create/archive: write PG + project.json atomically (2PC или PG first + compensating delete)
- Stats: periodic job scan `runs/*/status.json`

## RLS

```sql
CREATE POLICY projects_cabinet ON projects
  USING (
    tenant_id = current_setting('app.tenant_id', true)
    AND cabinet_id::text = current_setting('app.cabinet_id', true)
  );
```
