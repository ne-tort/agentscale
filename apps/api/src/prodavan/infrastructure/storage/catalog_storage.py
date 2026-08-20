"""User catalog filesystem layout."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from prodavan.config.settings import settings

CATALOG_SQLITE_SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS products (
  part_number TEXT NOT NULL,
  title TEXT,
  price REAL,
  currency TEXT NOT NULL DEFAULT 'RUB',
  stock TEXT,
  in_stock INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_products_pn ON products(part_number);
"""


def user_catalogs_root(tenant_id: uuid.UUID, cabinet_id: uuid.UUID) -> Path:
    return (
        settings.storage_root
        / "cabinets"
        / str(tenant_id)
        / str(cabinet_id)
        / "catalogs"
        / "user"
    )


def catalog_dir(tenant_id: uuid.UUID, cabinet_id: uuid.UUID, slug: str) -> Path:
    return user_catalogs_root(tenant_id, cabinet_id) / slug


def vault_path(tenant_id: uuid.UUID, cabinet_id: uuid.UUID) -> Path:
    return (
        settings.storage_root
        / "tenants"
        / str(tenant_id)
        / "cabinets"
        / str(cabinet_id)
        / "vault"
        / "s4b.json"
    )


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
