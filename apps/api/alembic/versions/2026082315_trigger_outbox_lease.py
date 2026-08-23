"""Trigger outbox lease columns for crash-safe claim (L07)."""

import sqlalchemy as sa

from alembic import op

revision = "2026082315"
down_revision = "2026082314"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_triggers",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "project_triggers",
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "project_triggers",
        sa.Column("leased_by", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "project_triggers",
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "project_triggers",
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_project_triggers_claim",
        "project_triggers",
        ["status", "available_at", "lease_until"],
    )


def downgrade() -> None:
    op.drop_index("ix_project_triggers_claim", table_name="project_triggers")
    op.drop_column("project_triggers", "last_error")
    op.drop_column("project_triggers", "available_at")
    op.drop_column("project_triggers", "leased_by")
    op.drop_column("project_triggers", "lease_until")
    op.drop_column("project_triggers", "attempts")
