"""AI key check results — stores last probe data per key (PROBE-P1)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "2026091801"
down_revision = "2026091602"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_key_check_results",
        sa.Column(
            "key_id",
            sa.String(length=40),
            sa.ForeignKey("ai_provider_keys.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=True),
        sa.Column("latency_ms", sa.Numeric(10, 0), nullable=True),
        sa.Column("models", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("default_model", sa.Text, nullable=True),
        sa.Column("http_status", sa.Numeric(6, 0), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("api_kind", sa.String(length=64), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("checked_by", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("ai_key_check_results")
