"""Unit tests — RO helpers for materialized catalog SQLite files."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from prodavan.application.modules.catalog_sqlite_query import (
    list_catalog_sqlite_files,
    query_catalog_sqlite,
)


def _write_catalog(path: Path, rows: list[tuple[str, str, str]]) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE rows (name TEXT, part_number TEXT, price TEXT)"
    )
    conn.executemany(
        "INSERT INTO rows (name, part_number, price) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def test_list_and_query_catalog_sqlite(tmp_path: Path) -> None:
    catalogs = tmp_path / "catalogs"
    catalogs.mkdir()
    db = catalogs / "cat1.sqlite"
    _write_catalog(
        db,
        [
            ("Mouse", "M1", "10"),
            ("Keyboard", "K1", "20"),
            ("Mouse Pad", "MP1", "5"),
        ],
    )
    assert list_catalog_sqlite_files(tmp_path) == [db]

    exact = query_catalog_sqlite(db, part_number="m1", limit=5)
    assert len(exact) == 1
    assert exact[0].catalog_id == "cat1"
    assert exact[0].values["name"] == "Mouse"

    text = query_catalog_sqlite(db, text_query="Mouse", limit=10)
    assert {h.values["part_number"] for h in text} == {"M1", "MP1"}
