"""Unit tests for merge_mapped_sqlite helper."""

from __future__ import annotations

import sqlite3

from prodavan.application.content.merge_mapped_sqlite import (
    column_map_is_complete,
    merge_mapped_sqlite_bytes,
)
from prodavan.application.content.tabular_index import _rows_to_sqlite


def test_column_map_is_complete() -> None:
    assert column_map_is_complete(
        {"title": "name", "price": "cost"},
        required_keys=["title", "price"],
    )
    assert not column_map_is_complete(
        {"title": "name"},
        required_keys=["title", "price"],
    )


def test_merge_mapped_sqlite_bytes_normalizes_and_provenance() -> None:
    raw_a = _rows_to_sqlite(
        ["product_name", "unit_price", "sku"],
        [["Мышь", "100", "M1"], ["Клавиатура", "200", "K1"]],
    ).sqlite_bytes
    raw_b = _rows_to_sqlite(
        ["title", "price"],
        [["Pad", "50"]],
    ).sqlite_bytes

    blob, count, digest = merge_mapped_sqlite_bytes(
        [
            {
                "name": "Vendor A",
                "column_map": {
                    "title": "product_name",
                    "price": "unit_price",
                    "part_number": "sku",
                },
                "_artifact_bytes": raw_a,
            },
            {
                "name": "Vendor B",
                "column_map": {"title": "title", "price": "price"},
                "_artifact_bytes": raw_b,
            },
            {
                "name": "Incomplete",
                "column_map": {"title": "title"},
                "_artifact_bytes": raw_b,
            },
        ]
    )
    assert count == 3
    assert len(digest) == 64

    conn = sqlite3.connect(":memory:")
    try:
        conn.deserialize(blob)
        rows = conn.execute(
            "SELECT title, price, part_number, source_catalog FROM rows ORDER BY title"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [
        ("Pad", "50", "", "Vendor B"),
        ("Клавиатура", "200", "K1", "Vendor A"),
        ("Мышь", "100", "M1", "Vendor A"),
    ]
