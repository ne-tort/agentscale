"""RO helpers for materialized equipment/catalog SQLite files in workspace."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CatalogQueryHit:
    catalog_id: str
    values: dict[str, str]


def list_catalog_sqlite_files(workspace_root: Path) -> list[Path]:
    root = workspace_root / "catalogs"
    if not root.is_dir():
        return []
    merged = root / "catalog.sqlite"
    if merged.is_file():
        return [merged]
    return sorted(p for p in root.glob("*.sqlite") if p.is_file())


def query_catalog_sqlite(
    db_path: Path,
    *,
    part_number: str | None = None,
    text_query: str | None = None,
    limit: int = 20,
) -> list[CatalogQueryHit]:
    """Read-only SELECT against table `rows`. Prefer canonical columns when present."""
    limit = max(1, min(int(limit), 200))
    uri = f"file:{db_path.as_posix()}?mode=ro"
    hits: list[CatalogQueryHit] = []
    conn = sqlite3.connect(uri, uri=True)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(rows)").fetchall()]
        if not cols:
            return []
        col_sql = ", ".join(f'"{c}"' for c in cols)
        pn = (part_number or "").strip()
        tq = (text_query or "").strip()
        if pn and "part_number" in cols:
            rows = conn.execute(
                f'SELECT {col_sql} FROM rows WHERE lower("part_number") = lower(?) LIMIT ?',
                (pn, limit),
            ).fetchall()
        elif pn:
            clauses = " OR ".join(f'lower("{c}") = lower(?)' for c in cols)
            params = [pn] * len(cols) + [limit]
            rows = conn.execute(
                f"SELECT {col_sql} FROM rows WHERE {clauses} LIMIT ?",
                params,
            ).fetchall()
        elif tq and "title" in cols:
            rows = conn.execute(
                f'SELECT {col_sql} FROM rows WHERE "title" LIKE ? LIMIT ?',
                (f"%{tq}%", limit),
            ).fetchall()
        elif tq:
            clauses = " OR ".join(f'"{c}" LIKE ?' for c in cols)
            like = f"%{tq}%"
            params = [like] * len(cols) + [limit]
            rows = conn.execute(
                f"SELECT {col_sql} FROM rows WHERE {clauses} LIMIT ?",
                params,
            ).fetchall()
        else:
            rows = conn.execute(f"SELECT {col_sql} FROM rows LIMIT ?", (limit,)).fetchall()
        for row in rows:
            values = {cols[i]: str(row[i] if row[i] is not None else "") for i in range(len(cols))}
            catalog_id = values.get("source_catalog") or db_path.stem
            hits.append(CatalogQueryHit(catalog_id=catalog_id, values=values))
    finally:
        conn.close()
    return hits
