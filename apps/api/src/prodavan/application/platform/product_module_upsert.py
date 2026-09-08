"""Idempotent upsert of product modules from PRODUCT_MODULES seeds.

Call from Alembic migrations whenever seeds change. Do not silent-overwrite
from API bootstrap (would clobber manual platform meta edits).
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from prodavan.application.platform.product_module_seeds import PRODUCT_MODULES


def upsert_product_modules(conn: sa.Connection) -> int:
    """Upsert all PRODUCT_MODULES rows + meta docs. Returns number of modules."""
    count = 0
    for module_id, name, slugs in PRODUCT_MODULES:
        _upsert_one(conn, module_id=module_id, name=name, slugs=slugs)
        count += 1
    # Refresh instance meta when instance tables exist (post-0503).
    # Product module seeds are SoT — propagate to every owner instance (platform /
    # company / cabinet / project). Leaving only platform refreshed leaves bound
    # cabinets on stale UX after seed updates.
    if _has_table(conn, "module_instances"):
        for module_id, _, slugs in PRODUCT_MODULES:
            _refresh_all_instance_meta(conn, module_id=module_id, slugs=slugs)
            _apply_seed_rows_to_instances(conn, module_id=module_id, slugs=slugs)
    return count


def _apply_seed_rows_to_instances(
    conn: sa.Connection,
    *,
    module_id: str,
    slugs: dict[str, Any],
) -> None:
    """Insert missing template seed_rows into every instance (never overwrite body)."""
    import uuid

    if not _has_table(conn, "module_instance_data_rows"):
        return
    seed = slugs.get("seed_rows")
    if not isinstance(seed, dict):
        return
    items = seed.get("items")
    if not isinstance(items, list) or not items:
        return
    rows = conn.execute(
        sa.text("SELECT id FROM module_instances WHERE module_id = :mid"),
        {"mid": module_id},
    ).fetchall()
    for row in rows:
        instance_id = str(row[0])
        for item in items:
            if not isinstance(item, dict):
                continue
            table_slug = item.get("table_slug")
            row_id = item.get("row_id")
            body = item.get("body", {})
            if not isinstance(table_slug, str) or not isinstance(row_id, str):
                continue
            if not isinstance(body, dict):
                body = {}
            conn.execute(
                sa.text(
                    """
                    INSERT INTO module_instance_data_rows
                        (id, instance_id, table_slug, row_id, body, created_by)
                    VALUES
                        (:id, :iid, :ts, :rid, CAST(:body AS jsonb), :cb)
                    ON CONFLICT (instance_id, table_slug, row_id) DO NOTHING
                    """
                ),
                {
                    "id": f"midr_{uuid.uuid4().hex[:16]}",
                    "iid": instance_id,
                    "ts": table_slug,
                    "rid": row_id,
                    "body": json.dumps(body),
                    "cb": "module_seed",
                },
            )

def _has_table(conn: sa.Connection, name: str) -> bool:
    row = conn.execute(
        sa.text(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = :n
            """
        ),
        {"n": name},
    ).fetchone()
    return row is not None


def _refresh_all_instance_meta(
    conn: sa.Connection,
    *,
    module_id: str,
    slugs: dict[str, Any],
) -> None:
    """Overwrite meta documents for every instance of a product module from seed."""
    import uuid

    rows = conn.execute(
        sa.text("SELECT id FROM module_instances WHERE module_id = :mid"),
        {"mid": module_id},
    ).fetchall()
    for row in rows:
        instance_id = str(row[0])
        for slug, body in slugs.items():
            conn.execute(
                sa.text(
                    """
                    INSERT INTO module_instance_meta_documents (id, instance_id, slug, body)
                    VALUES (:id, :iid, :slug, CAST(:body AS jsonb))
                    ON CONFLICT (instance_id, slug) DO UPDATE SET body = EXCLUDED.body
                    """
                ),
                {
                    "id": f"mimd_{uuid.uuid4().hex[:16]}",
                    "iid": instance_id,
                    "slug": slug,
                    "body": json.dumps(body),
                },
            )


def _refresh_platform_instance_meta(
    conn: sa.Connection,
    *,
    module_id: str,
    slugs: dict[str, Any],
) -> None:
    """Backward-compatible alias — refresh all instances for the module."""
    _refresh_all_instance_meta(conn, module_id=module_id, slugs=slugs)


def _upsert_one(
    conn: sa.Connection,
    *,
    module_id: str,
    name: str,
    slugs: dict[str, Any],
) -> None:
    conn.execute(
        sa.text(
            """
            INSERT INTO modules (id, name, status)
            VALUES (:id, :name, 'active')
            ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, status = 'active'
            """
        ),
        {"id": module_id, "name": name},
    )
    for slug, body in slugs.items():
        doc_id = f"mmd_{module_id.removeprefix('mod_')}_{slug}"
        conn.execute(
            sa.text(
                """
                INSERT INTO module_meta_documents (id, module_id, slug, body)
                VALUES (:id, :module_id, :slug, CAST(:body AS jsonb))
                ON CONFLICT (module_id, slug) DO UPDATE SET body = EXCLUDED.body
                """
            ),
            {
                "id": doc_id,
                "module_id": module_id,
                "slug": slug,
                "body": json.dumps(body),
            },
        )
