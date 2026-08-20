# M02 — API: спеки и КП

Active project required. Base: `/v1/projects/{pid}`.

## POST /v1/projects/{pid}/inbox/upload

Multipart upload spec file.

**Request:** `file` (xlsx|xls|csv|txt), optional `auto_run: true`

**Response 201:**

```json
{
  "filename": "spec-client-alpha.xlsx",
  "size_bytes": 45678,
  "sha256": "abc...",
  "extracted_md": "spec-client-alpha.xlsx.extracted.md",
  "run_id": "01JABC1234567890",
  "run_phase": "ingest"
}
```

---

## POST /v1/projects/{pid}/runs

Create run from inbox file.

**Request:**

```json
{
  "input_filename": "spec-client-alpha.xlsx"
}
```

**Response 201:**

```json
{
  "run_id": "01JABC1234567890",
  "workspace_key": "cab:acme-corp:0195a1b2-...:proj_7f3a9c2e",
  "phase": "ingest",
  "status_uri": "prodavan://.../runs/01JABC1234567890/status.json"
}
```

---

## GET /v1/projects/{pid}/runs/{run_id}

Full run status + artifact links.

**Response 200:**

```json
{
  "run_id": "01JABC1234567890",
  "phase": "search",
  "phase_status": "running",
  "artifacts": {
    "rows": "runs/01JABC.../rows.json",
    "lineitems": "runs/01JABC.../lineitems.json",
    "offers": null,
    "selection": null
  },
  "stats": { "rows": 45, "line_items": 42, "offers": 0, "needs_review": 3 }
}
```

---

## POST /v1/projects/{pid}/runs/{run_id}/advance

Trigger next pipeline phase (or specific).

**Request:**

```json
{
  "target_phase": "classify",
  "force": false
}
```

**Response 202:** job accepted

```json
{
  "job_id": "job_xyz",
  "from_phase": "ingest",
  "to_phase": "classify"
}
```

Errors: `PHASE_GUARD_FAILED`, `RUN_BLOCKED`, `CAPABILITY_MISSING`.

---

## POST /v1/projects/{pid}/runs/{run_id}/re-search

From review: rerun search + rank + variants.

**Request:**

```json
{
  "line_ids": ["line_001", "line_002"],
  "sources": ["catalog", "s4b", "web"]
}
```

Note: `s4b` silently dropped if cabinet not electronics.

---

## GET /v1/projects/{pid}/runs/{run_id}/lineitems

Paginated LineItems.

---

## GET /v1/projects/{pid}/runs/{run_id}/offers

Filter: `line_id`, `source`, `in_stock=true`

---

## GET /v1/projects/{pid}/variants

From commerce.sqlite (post variants phase).

**Query:** `line_id`, `role=primary|alternative`

**Response:**

```json
{
  "items": [
    {
      "line_id": "line_001",
      "role": "primary",
      "part_number": "910-001793",
      "seller": "distrib-trusted",
      "price": 890.0,
      "currency": "RUB",
      "score": 0.95
    }
  ]
}
```

---

## POST /v1/projects/{pid}/runs/{run_id}/finalize

Operator approval → phase final.

**Request:**

```json
{
  "operator_note": "OK for KP",
  "confirmed": true
}
```

Requires scope `specs:finalize`.

---

## POST /v1/projects/{pid}/export/kp

Generate KP xlsx (bot-equivalent).

**Request:**

```json
{
  "run_id": "01JABC1234567890",
  "template": "default",
  "include_alternatives": true
}
```

**Response 200:**

```json
{
  "export_path": "export/kp-01JABC1234567890-20260820T070000.xlsx",
  "download_url": "/v1/projects/{pid}/export/kp-01JABC...xlsx",
  "lines_filled": 42,
  "lines_review": 3
}
```

---

## Equipment cards (electronics only)

### GET /v1/projects/{pid}/equipment

List cards from commerce.sqlite.

### POST /v1/projects/{pid}/equipment

Upsert card (usually via agent MCP).

**Request:**

```json
{
  "part_number": "CMK32GX5M2B5600C36",
  "category": "ram",
  "specs": { "ddr": "DDR5", "capacity_gb": 32, "form_factor": "DIMM" }
}
```

**Error:** `CAPABILITY_MISSING` 403 on non-electronics.

---

## Webhook / SSE

`GET /v1/projects/{pid}/runs/{run_id}/events` — SSE phase transitions.

## CLI mapping (legacy)

| CLI | API equivalent |
| --- | --- |
| new_run.py | POST /runs |
| parse_spec.py | advance ingest→classify |
| classify_rows.py | advance classify |
| search_offers.py | advance search |
| rank_offers.py | advance rank |
| commerce_db.py import-run | advance variants |
| kp_export.py | POST /export/kp |
