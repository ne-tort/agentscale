"""AI model catalog: key_aliases + provider/reasoning/description (MODELS-L1)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026092001"
down_revision = "2026091801"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_models",
        sa.Column(
            "key_aliases",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("ai_models", sa.Column("provider", sa.String(length=64), nullable=True))
    op.add_column("ai_models", sa.Column("reasoning_level", sa.String(length=32), nullable=True))
    op.add_column("ai_models", sa.Column("description", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_models", "description")
    op.drop_column("ai_models", "reasoning_level")
    op.drop_column("ai_models", "provider")
    op.drop_column("ai_models", "key_aliases")
