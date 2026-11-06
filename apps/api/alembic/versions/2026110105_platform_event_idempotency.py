"""Add durable idempotency identity to platform lifecycle events."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026110105"
down_revision = "2026110104"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "platform_events",
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
    )
    op.create_unique_constraint(
        "uq_platform_events_idempotency_key",
        "platform_events",
        ["idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_platform_events_idempotency_key",
        "platform_events",
        type_="unique",
    )
    op.drop_column("platform_events", "idempotency_key")
