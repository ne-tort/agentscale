"""Idle pause hours for company agent policy (L04/L07/L09)."""

import sqlalchemy as sa

from alembic import op

revision = "2026082316"
down_revision = "2026082315"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "company_agent_runtime_policies",
        sa.Column("idle_pause_after_hours", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("company_agent_runtime_policies", "idle_pause_after_hours")
