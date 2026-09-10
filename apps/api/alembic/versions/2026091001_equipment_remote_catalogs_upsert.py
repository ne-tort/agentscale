"""Upsert product modules — equipment remote catalogs (source_kind).

PR #292 updated PRODUCT_MODULES seeds (source_kind local|remote, remote_dsn,
remote_table, probe actions, foreach env) but shipped without an Alembic
upsert, so platform + all instance meta stayed on the pre-remote shape.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026091001"
down_revision = "2026090901"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)
    # Existing catalog rows predate source_kind; default to local so UI/actions
    # see a concrete value (column default alone does not rewrite JSON bodies).
    conn.execute(
        sa.text(
            """
            UPDATE module_instance_data_rows
            SET body = body || CAST(:patch AS jsonb)
            WHERE table_slug = 'catalogs'
              AND NOT (body ? 'source_kind')
            """
        ),
        {"patch": '{"source_kind": "local"}'},
    )


def downgrade() -> None:
    pass
