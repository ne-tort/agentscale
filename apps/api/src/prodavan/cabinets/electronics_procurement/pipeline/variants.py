"""Import run artifacts into project commerce.sqlite (no invented variants)."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from prodavan.infrastructure.storage.project_storage import project_root


def import_run_to_sqlite(
    *,
    tenant_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    project_id: str,
    run_id: str,
    lineitems: list[dict],
    offers: list[dict],
    selections: list[dict],
) -> int:
    db_path: Path = project_root(tenant_id, cabinet_id, project_id) / "commerce.sqlite"
    now = datetime.now(UTC).isoformat()
    imported = 0
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("DELETE FROM variants WHERE lineitem_id IN (SELECT id FROM lineitems WHERE run_id = ?)", (run_id,))
        conn.execute("DELETE FROM lineitems WHERE run_id = ?", (run_id,))
        for item in lineitems:
            conn.execute(
                """
                INSERT INTO lineitems (id, run_id, seq, raw_text, qty, category, part_number, constraints_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["line_id"],
                    run_id,
                    int(item["line_id"].split("_")[-1]),
                    item.get("raw_text"),
                    item.get("qty"),
                    item.get("category"),
                    item.get("part_number"),
                    "[]",
                    now,
                    now,
                ),
            )
            imported += 1
        primary_by_line = {s["line_id"]: s.get("primary_offer_id") for s in selections}
        offer_by_id = {o["offer_id"]: o for o in offers}
        for line_id, offer_id in primary_by_line.items():
            offer = offer_by_id.get(offer_id)
            if not offer:
                continue
            conn.execute(
                """
                INSERT INTO variants (
                    id, lineitem_id, supplier, seller, sku, part_number, title, price, currency,
                    availability, match_type, source_type, is_best, as_of, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    offer["offer_id"],
                    line_id,
                    offer.get("seller"),
                    offer.get("seller"),
                    offer.get("sku"),
                    offer.get("part_number"),
                    offer.get("title"),
                    offer.get("price"),
                    offer.get("currency"),
                    "in_stock" if offer.get("in_stock") else "unknown",
                    offer.get("match_type"),
                    offer.get("source"),
                    now,
                    now,
                    now,
                ),
            )
        conn.commit()
    return imported
