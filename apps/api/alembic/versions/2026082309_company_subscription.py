"""Add company subscription fields (L04)."""

from alembic import op
import sqlalchemy as sa

revision = "2026082309"
down_revision = "agent_budget_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("subscription_ends_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "companies",
        sa.Column(
            "subscription_lifetime",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("companies", "subscription_lifetime")
    op.drop_column("companies", "subscription_ends_at")
