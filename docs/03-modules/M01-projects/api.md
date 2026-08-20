# M01 — API: проекты

Префикс: `/v1`. Требуется active cabinet (`X-Cabinet-Id` или JWT `cab`).

## GET /v1/projects

**Query:** `status`, `q` (search name), `limit`, `cursor`

**Response 200:**

```json
{
  "cabinet_id": "0195a1b2-c3d4-7890-abcd-ef1234567890",
  "items": [
    {
      "id": "proj_7f3a9c2e",
      "slug": "client-alpha",
      "display_name": "Клиент Alpha",
      "status": "active",
      "workspace_key": "cab:acme-corp:0195a1b2-c3d4-7890-abcd-ef1234567890:proj_7f3a9c2e",
      "last_opened_at": "2026-08-19T14:00:00Z",
      "stats": { "runs_total": 12, "runs_active": 1, "inbox_pending": 2 }
    }
  ]
}
```

---

## POST /v1/projects

**Request:**

```json
{
  "slug": "client-alpha",
  "display_name": "Клиент Alpha"
}
```

**Response 201:**

```json
{
  "id": "proj_7f3a9c2e",
  "slug": "client-alpha",
  "display_name": "Клиент Alpha",
  "workspace_key": "cab:acme-corp:0195a1b2-c3d4-7890-abcd-ef1234567890:proj_7f3a9c2e",
  "storage_uri": "prodavan://storage/cabinets/acme-corp/0195a1b2-.../projects/proj_7f3a9c2e/",
  "status": "active",
  "created_at": "2026-08-20T07:00:00Z"
}
```

Side effects: создаёт `inbox/`, `runs/`, `export/`, пустой `commerce.sqlite`, `project.json`.

---

## GET /v1/projects/{pid}

---

## PATCH /v1/projects/{pid}

Разрешены: `display_name`, `metadata`.

---

## DELETE /v1/projects/{pid}

Archive → status `archived`.

---

## POST /v1/projects/{pid}/open

Установить активный проект сессии.

**Request:**

```json
{
  "session_id": "sess_abc123"
}
```

**Response 200:**

```json
{
  "project_id": "proj_7f3a9c2e",
  "workspace_key": "cab:acme-corp:0195a1b2-c3d4-7890-abcd-ef1234567890:proj_7f3a9c2e",
  "storage_paths": {
    "inbox": "prodavan://.../inbox/",
    "runs": "prodavan://.../runs/",
    "export": "prodavan://.../export/",
    "commerce_db": "prodavan://.../commerce.sqlite"
  },
  "opened_at": "2026-08-20T07:05:00Z"
}
```

---

## POST /v1/projects/{pid}/restore

---

## GET /v1/projects/{pid}/stats

Aggregates для dashboard.

**Response:**

```json
{
  "runs_by_phase": {
    "ingest": 0,
    "classify": 1,
    "search": 2,
    "rank": 1,
    "variants": 3,
    "review": 5,
    "final": 8
  },
  "inbox_files": 2,
  "export_files": 4
}
```

## Errors

| Code | HTTP |
| --- | --- |
| `PROJECT_NOT_FOUND` | 404 |
| `PROJECT_ARCHIVED` | 409 |
| `CABINET_MISMATCH` | 403 |
| `SLUG_CONFLICT` | 409 |
| `NO_ACTIVE_CABINET` | 400 |
