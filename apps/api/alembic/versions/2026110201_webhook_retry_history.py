"""Preserve webhook delivery attempts across manual retries."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "2026110201"
down_revision = "2026110105"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "webhook_deliveries",
        sa.Column("retry_history", JSONB, nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("webhook_deliveries", "retry_history")
