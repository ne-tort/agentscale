"""Add project_pods.last_started_at for cached last launch time."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "2026082908"
down_revision = "2026082907"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_pods",
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("project_pods", "last_started_at")
