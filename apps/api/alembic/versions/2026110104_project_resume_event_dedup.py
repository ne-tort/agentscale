"""Persist project resume generation for lifecycle event deduplication."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026110104"
down_revision = "2026110103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("resume_generation", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("projects", "resume_generation")
