"""Project-owned cron schedules for durable system.schedule triggers."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "2026110101"
down_revision = "2026103001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_schedules",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("cron", sa.String(length=120), nullable=False),
        sa.Column("timezone", sa.String(length=64), server_default="UTC", nullable=False),
        sa.Column("payload", JSONB(), server_default="{}", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_schedules_due", "project_schedules", ["enabled", "next_run_at"])
    op.create_index("ix_project_schedules_project", "project_schedules", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_project_schedules_project", table_name="project_schedules")
    op.drop_index("ix_project_schedules_due", table_name="project_schedules")
    op.drop_table("project_schedules")
