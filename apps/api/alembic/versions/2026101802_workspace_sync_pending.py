"""WAVE16: projects.workspace_sync_pending_at/source — отложенный sync.

Rematerialize при активном ходе агента больше не прерывает сессию: проект
помечается pending, sync исполняется при окончании хода (turn-end hook) или
sweep'ом по таймауту.
"""

import sqlalchemy as sa
from alembic import op

from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026101802"
down_revision = "2026101801"


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("workspace_sync_pending_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("workspace_sync_pending_source", sa.String(128), nullable=True),
    )
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    op.drop_column("projects", "workspace_sync_pending_source")
    op.drop_column("projects", "workspace_sync_pending_at")
