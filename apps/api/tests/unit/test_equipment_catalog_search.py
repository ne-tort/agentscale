"""Unit tests for unified equipment catalog search."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from prodavan.application.modules.equipment_catalog_search import (
    CatalogSource,
    apply_column_map,
    is_in_stock,
    list_catalog_sources,
    parse_column_map,
    query_local_sqlite,
    query_remote_postgres,
    search_catalogs,
)


def test_is_in_stock_denylist() -> None:
    assert is_in_stock(None) is False
    assert is_in_stock("") is False
    assert is_in_stock("   ") is False
    assert is_in_stock("-") is False
    assert is_in_stock("—") is False
    assert is_in_stock("нет") is False
    assert is_in_stock("NO") is False
    assert is_in_stock("под заказ") is False
    assert is_in_stock("под  заказ") is False
    assert is_in_stock("on_order") is False
    assert is_in_stock("3 дня") is True
    assert is_in_stock("в наличии") is True
    assert is_in_stock("10") is True


def test_apply_column_map() -> None:
    cmap = parse_column_map({"title": "Name", "price": "Cost", "part_number": "PN"})
    mapped = apply_column_map({"Name": "Mouse", "Cost": "12.5", "PN": "M185"}, cmap)
    assert mapped["title"] == "Mouse"
    assert mapped["price"] == "12.5"
    assert mapped["part_number"] == "M185"
    assert mapped["brand"] == ""


def _write_local_catalog(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path.as_posix())
    conn.execute(
        'CREATE TABLE rows ("title" TEXT, "price" TEXT, "part_number" TEXT, '
        '"brand" TEXT, "supplier" TEXT, "lead_time" TEXT, "source_catalog" TEXT)'
    )
    rows = [
        ("Mouse A", "100", "M-100", "Logi", "S1", "3 дня", "local-a"),
        ("Mouse B", "80", "M-100", "Logi", "S1", "нет", "local-a"),
        ("Keyboard", "50", "K-1", "Key", "S1", "-", "local-a"),
        ("Mouse C", "90", "M-200", "Logi", "S1", "1 день", "local-b"),
    ]
    conn.executemany("INSERT INTO rows VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def test_local_search_exact_pn_in_stock_and_price_sort(tmp_path: Path) -> None:
    db = tmp_path / "catalogs" / "catalog.sqlite"
    _write_local_catalog(db)
    src = CatalogSource(
        id="local-merged",
        name="Local",
        kind="local",
        column_map={k: k for k in ("title", "price", "part_number", "brand", "supplier", "lead_time")},
        sqlite_path=db,
    )
    hits = query_local_sqlite(src, part_number="M-100", in_stock_only=True)
    assert len(hits) == 1
    assert hits[0].values["title"] == "Mouse A"
    assert hits[0].in_stock is True
    assert hits[0].match_rank == 0


def test_search_catalogs_merge_rank_pagination(tmp_path: Path) -> None:
    db = tmp_path / "catalogs" / "catalog.sqlite"
    _write_local_catalog(db)
    local = CatalogSource(
        id="local-merged",
        name="Local",
        kind="local",
        column_map={k: k for k in ("title", "price", "part_number", "brand", "supplier", "lead_time")},
        sqlite_path=db,
    )
    remote = CatalogSource(
        id="remote-1",
        name="S4B",
        kind="remote",
        column_map={
            "title": "name",
            "price": "price",
            "part_number": "pn",
            "lead_time": "avail",
        },
        dsn="postgresql://x",
        table="public.supplier_price_items",
        row_count=1_000_000,
    )

    def fake_pg(dsn: str, sql: str, params: list) -> list[dict]:
        assert "LIMIT" in sql.upper()
        return [
            {"title": "Mouse R", "price": "70", "part_number": "M-100", "brand": "", "supplier": "", "lead_time": "склад"},
        ]

    out = search_catalogs(
        [local, remote],
        part_number="M-100",
        in_stock_only=True,
        limit=10,
        offset=0,
        remote_query_fn=fake_pg,
    )
    titles = [i["title"] for i in out["items"]]
    # Remote cheaper in-stock first among exact_pn (price 70 then 100)
    assert titles[0] == "Mouse R"
    assert titles[1] == "Mouse A"
    assert all(i["in_stock"] for i in out["items"])


def test_list_sources_from_env(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "catalogs" / "catalog.sqlite"
    _write_local_catalog(db)
    registry = [
        {
            "id": "r1",
            "name": "Remote",
            "table": "public.supplier_price_items",
            "column_map": {"title": "t", "price": "p"},
            "dsn_env": "EQUIPMENT_CATALOG_DSN_r1",
            "row_count": 10,
        }
    ]
    env = {
        "EQUIPMENT_REMOTE_CATALOGS": json.dumps(registry),
        "EQUIPMENT_CATALOG_DSN_r1": "postgresql://u:p@h/db",
    }
    sources = list_catalog_sources(tmp_path, environ=env)
    assert sources[0].kind == "local"
    assert sources[1].id == "r1"
    assert sources[1].dsn.startswith("postgresql://")


def test_remote_skips_open_scan_on_huge_table() -> None:
    remote = CatalogSource(
        id="r1",
        name="Huge",
        kind="remote",
        column_map={"title": "name", "price": "price"},
        dsn="postgresql://x",
        table="public.supplier_price_items",
        row_count=2_000_000,
    )
    called = {"n": 0}

    def fake_pg(*_a, **_k):
        called["n"] += 1
        return []

    hits = query_remote_postgres(remote, query_fn=fake_pg)
    assert hits == []
    assert called["n"] == 0
