"""Add company agent policy max_attachment_mb (L04/L07)."""

from alembic import op
import sqlalchemy as sa

revision = "2026082311"
down_revision = "2026082310"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "company_agent_runtime_policies",
        sa.Column("max_attachment_mb", sa.Integer(), nullable=False, server_default="20"),
    )


def downgrade() -> None:
    op.drop_column("company_agent_runtime_policies", "max_attachment_mb")
