"""Local filesystem storage for projects."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from prodavan.config.settings import settings

COMMERCE_SQLITE_SCHEMA = """
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS lineitems (
  n INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT NOT NULL UNIQUE,
  run_id TEXT,
  seq INTEGER,
  raw_text TEXT,
  qty REAL,
  category TEXT,
  part_number TEXT,
  constraints_json TEXT,
  created_at TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS variants (
  n INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT NOT NULL UNIQUE,
  lineitem_id TEXT NOT NULL,
  supplier TEXT,
  seller TEXT,
  seller_tier TEXT,
  sku TEXT,
  part_number TEXT,
  title TEXT,
  price REAL,
  currency TEXT,
  vat TEXT,
  availability TEXT,
  lead_time TEXT,
  match_type TEXT,
  relevance REAL,
  ai_confidence REAL,
  price_score REAL,
  source_type TEXT,
  source_ref TEXT,
  notes TEXT,
  is_best INTEGER NOT NULL DEFAULT 0,
  as_of TEXT,
  created_at TEXT,
  updated_at TEXT,
  FOREIGN KEY (lineitem_id) REFERENCES lineitems(id)
);

CREATE INDEX IF NOT EXISTS idx_var_li ON variants(lineitem_id);
CREATE INDEX IF NOT EXISTS idx_var_pn ON variants(part_number);
CREATE INDEX IF NOT EXISTS idx_var_best ON variants(lineitem_id, is_best);

CREATE TABLE IF NOT EXISTS specs (
  id TEXT PRIMARY KEY,
  part_number TEXT,
  category TEXT,
  manufacturer TEXT,
  model TEXT,
  title TEXT,
  attrs_json TEXT,
  notes TEXT,
  sources_json TEXT,
  compatibility_json TEXT,
  updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_specs_pn ON specs(part_number);

CREATE TABLE IF NOT EXISTS spec_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  spec_id TEXT NOT NULL,
  part_number TEXT,
  variant_n INTEGER,
  lineitem_id TEXT,
  UNIQUE (spec_id, part_number, variant_n)
);
"""


def project_root(tenant_id: uuid.UUID, cabinet_id: uuid.UUID, project_id: str) -> Path:
    return (
        settings.storage_root
        / "cabinets"
        / str(tenant_id)
        / str(cabinet_id)
        / "projects"
        / project_id
    )


def project_storage_uri(tenant_id: uuid.UUID, cabinet_id: uuid.UUID, project_id: str) -> str:
    return (
        f"prodavan://storage/cabinets/{tenant_id}/{cabinet_id}/projects/{project_id}/"
    )


def init_project_storage(
    *,
    tenant_id: uuid.UUID,
    tenant_slug: str,
    cabinet_id: uuid.UUID,
    project_id: str,
    slug: str,
    display_name: str,
    workspace_key: str,
) -> Path:
    root = project_root(tenant_id, cabinet_id, project_id)
    for sub in ("inbox", "runs", "export"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC).isoformat()
    marker = {
        "id": project_id,
        "cabinet_id": str(cabinet_id),
        "tenant_id": tenant_slug,
        "slug": slug,
        "display_name": display_name,
        "workspace_key": workspace_key,
        "status": "active",
        "schema_version": 1,
        "created_at": now,
        "updated_at": now,
    }
    tmp = root / "project.json.tmp"
    final = root / "project.json"
    tmp.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(final)

    db_path = root / "commerce.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(COMMERCE_SQLITE_SCHEMA)

    return root


def remove_project_storage(
    tenant_id: uuid.UUID, cabinet_id: uuid.UUID, project_id: str
) -> None:
    root = project_root(tenant_id, cabinet_id, project_id)
    if root.exists():
        import shutil

        shutil.rmtree(root)


def scan_project_stats(tenant_id: uuid.UUID, cabinet_id: uuid.UUID, project_id: str) -> dict:
    root = project_root(tenant_id, cabinet_id, project_id)
    inbox = root / "inbox"
    runs = root / "runs"
    export = root / "export"

    inbox_pending = 0
    if inbox.exists():
        inbox_pending = sum(1 for p in inbox.iterdir() if p.is_file())

    runs_total = 0
    runs_active = 0
    runs_by_phase: dict[str, int] = {}
    if runs.exists():
        for run_dir in runs.iterdir():
            if not run_dir.is_dir():
                continue
            runs_total += 1
            status_path = run_dir / "status.json"
            phase = "unknown"
            is_final = False
            if status_path.exists():
                try:
                    data = json.loads(status_path.read_text(encoding="utf-8"))
                    phase = str(data.get("phase", "unknown"))
                    is_final = data.get("final") is True or phase == "final"
                except (json.JSONDecodeError, OSError):
                    pass
            runs_by_phase[phase] = runs_by_phase.get(phase, 0) + 1
            if not is_final:
                runs_active += 1

    export_files = 0
    if export.exists():
        export_files = sum(1 for p in export.rglob("*") if p.is_file())

    return {
        "runs_total": runs_total,
        "runs_active": runs_active,
        "inbox_pending": inbox_pending,
        "export_files": export_files,
        "runs_by_phase": runs_by_phase,
    }
