"""Per-cabinet SQLite database provisioning and migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import UUID

from prodavan.config.settings import settings


def cabinet_db_path(tenant_id: UUID, cabinet_id: UUID) -> Path:
    return (
        settings.storage_root
        / "cabinets"
        / str(tenant_id)
        / str(cabinet_id)
        / "cabinet.sqlite"
    )


def ensure_cabinet_db(tenant_id: UUID, cabinet_id: UUID) -> Path:
    path = cabinet_db_path(tenant_id, cabinet_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "id TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        conn.commit()
        conn.close()
    return path


def apply_sql_migrations(db_path: Path, migrations_dir: Path) -> list[str]:
    if not migrations_dir.is_dir():
        return []
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "id TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {
            row[0]
            for row in conn.execute("SELECT id FROM schema_migrations").fetchall()
        }
        newly: list[str] = []
        for sql_file in sorted(migrations_dir.glob("*.sql")):
            mid = sql_file.name
            if mid in applied:
                continue
            script = sql_file.read_text(encoding="utf-8")
            conn.executescript(script)
            conn.execute(
                "INSERT INTO schema_migrations(id, applied_at) VALUES (?, datetime('now'))",
                (mid,),
            )
            newly.append(mid)
        conn.commit()
        return newly
    finally:
        conn.close()
