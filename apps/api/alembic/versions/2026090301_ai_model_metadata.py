"""AI model catalog metadata columns."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026090301"
down_revision = "2026090210"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_models", sa.Column("input_price_usd_per_mtok", sa.Numeric(12, 6), nullable=True))
    op.add_column("ai_models", sa.Column("output_price_usd_per_mtok", sa.Numeric(12, 6), nullable=True))
    op.add_column("ai_models", sa.Column("max_context_tokens", sa.Integer(), nullable=True))
    op.add_column("ai_models", sa.Column("publisher", sa.String(length=128), nullable=True))
    op.add_column("ai_models", sa.Column("released_at", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_models", "released_at")
    op.drop_column("ai_models", "publisher")
    op.drop_column("ai_models", "max_context_tokens")
    op.drop_column("ai_models", "output_price_usd_per_mtok")
    op.drop_column("ai_models", "input_price_usd_per_mtok")
