"""Add company agent policy max_cost_usd_month (L08)."""

from alembic import op
import sqlalchemy as sa

revision = "2026082310"
down_revision = "2026082309"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "company_agent_runtime_policies",
        sa.Column("max_cost_usd_month", sa.Numeric(12, 6), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("company_agent_runtime_policies", "max_cost_usd_month")
