# M02 — Persistence: спеки и КП

## status.json (per run)

```json
{
  "run_id": "01JABC1234567890",
  "workspace_key": "cab:acme-corp:0195a1b2-...:proj_7f3a9c2e",
  "input_file": "spec-client-alpha.xlsx",
  "phase": "rank",
  "phase_status": "completed",
  "phase_history": [
    { "phase": "ingest", "status": "completed", "at": "2026-08-20T07:10:00Z" },
    { "phase": "classify", "status": "completed", "at": "2026-08-20T07:12:00Z" },
    { "phase": "search", "status": "completed", "at": "2026-08-20T07:25:00Z" },
    { "phase": "rank", "status": "completed", "at": "2026-08-20T07:26:00Z" }
  ],
  "stats": {
    "rows": 45,
    "line_items": 42,
    "offers": 318,
    "needs_review": 3
  },
  "capabilities_snapshot": {
    "s4b": true,
    "equipment_cards": true
  }
}
```

## Artifact schemas (JSON)

### rows.json

```json
{
  "run_id": "...",
  "rows": [
    { "row_index": 2, "cells": { "name": "Мышь Logitech", "qty": "10" }, "raw_line": "..." }
  ]
}
```

### lineitems.json

```json
{
  "items": [ { "line_id": "line_001", "raw_text": "...", "category": "mouse", "qty": 10, "confidence": 0.85, "needs_review": false } ]
}
```

### offers.json

```json
{
  "offers": [ { "offer_id": "off_001", "line_id": "line_001", "source": "s4b", "in_stock": true, "price": 890, "currency": "RUB" } ],
  "sources_log_ref": "sources.log"
}
```

### selection.json

```json
{
  "selections": [ { "line_id": "line_001", "primary_offer_id": "off_001", "alternative_offer_ids": ["off_002"] } ]
}
```

## commerce.sqlite schema

```sql
CREATE TABLE line_items (
  line_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  raw_text TEXT NOT NULL,
  category TEXT,
  part_number TEXT,
  qty REAL,
  needs_review INTEGER DEFAULT 0,
  payload JSON
);

CREATE TABLE offers (
  offer_id TEXT PRIMARY KEY,
  line_id TEXT NOT NULL,
  run_id TEXT,
  part_number TEXT,
  seller TEXT,
  price REAL,
  currency TEXT,
  source TEXT,
  match_type TEXT,
  role TEXT,                    -- primary | alternative | candidate
  in_stock INTEGER,
  payload JSON
);

CREATE TABLE offer_scores (
  offer_id TEXT PRIMARY KEY,
  relevance REAL,
  price_rank INTEGER,
  trusted_bonus REAL,
  total_score REAL
);

CREATE TABLE equipment_cards (
  equipment_id TEXT PRIMARY KEY,
  part_number TEXT,
  category TEXT,
  specs JSON NOT NULL,
  compatibility JSON,
  analogs JSON,
  updated_at TEXT
);

CREATE INDEX idx_offers_line ON offers (line_id, role);
```

## Optional PG: run_jobs

```sql
CREATE TABLE spec_run_jobs (
  job_id UUID PRIMARY KEY,
  run_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  phase_from TEXT,
  phase_to TEXT,
  status TEXT,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  error JSONB
);
```

## Phase guard queries

Before advance to `rank`:

```sql
-- application-level: file exists offers.json AND JSON array length > 0 OR explicit empty_ok flag
```

## Import idempotency

`import-run` with same run_id: UPSERT offers by offer_id, don't duplicate line_items.
