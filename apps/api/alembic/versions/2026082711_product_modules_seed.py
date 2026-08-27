"""Replace example modules with product modules (Prompts, MCP, Files)."""

import json

import sqlalchemy as sa
from alembic import op

from prodavan.application.platform.product_module_seeds import (
    EXAMPLE_MODULE_IDS,
    PRODUCT_MODULES,
)

revision = "2026082711"
down_revision = "2026082710"
branch_labels = None
depends_on = None


def _insert_module(conn: sa.Connection, module_id: str, name: str, slugs: dict) -> None:
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


def upgrade() -> None:
    conn = op.get_bind()
    for module_id in EXAMPLE_MODULE_IDS:
        conn.execute(sa.text("DELETE FROM modules WHERE id = :id"), {"id": module_id})
    for module_id, name, slugs in PRODUCT_MODULES:
        _insert_module(conn, module_id, name, slugs)


def downgrade() -> None:
    conn = op.get_bind()
    for module_id, _, _ in PRODUCT_MODULES:
        conn.execute(sa.text("DELETE FROM modules WHERE id = :id"), {"id": module_id})
