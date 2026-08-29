"""Add projects.launch_phase for observable materialize stage."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "2026082907"
down_revision = "2026082906"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("launch_phase", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "launch_phase")
