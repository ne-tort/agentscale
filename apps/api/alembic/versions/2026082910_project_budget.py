"""Optional per-project agent token budget."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026082910"
down_revision = "2026082909"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("budget_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "budget_tokens")
