"""Migration 2026093003: refresh mod_equipment meta (chat-scoped views)."""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "2026093003"
down_revision = "2026093002"


def upgrade() -> None:
    conn = op.get_bind()
    from prodavan.application.platform.product_module_upsert import upsert_product_modules

    upsert_product_modules(conn)
    # Trim legacy NULL-session rows on chats=current tables: they predate the
    # chat scoping and surface in EVERY chat (global data). Re-bucket them
    # into the synthetic "main" session so real chats see only their own rows.
    conn.execute(
        text(
            """
            UPDATE module_instance_data_rows
            SET session_id = 'main'
            WHERE table_slug IN
                ('request_lines', 'found_offers', 'equipment_items', 'equipment_builds', 'budget_lines')
              AND session_id IS NULL
              AND instance_id IN (
                  SELECT id FROM module_instances WHERE module_id = 'mod_equipment'
              )
            """
        )
    )


def downgrade() -> None:
    pass
