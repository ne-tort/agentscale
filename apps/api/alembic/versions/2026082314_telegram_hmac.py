"""Add company telegram_hmac_secret for signed telegram.message ingress (L07)."""

from alembic import op
import sqlalchemy as sa

revision = "2026082314"
down_revision = "2026082313"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "company_agent_runtime_policies",
        sa.Column("telegram_hmac_secret", sa.String(length=256), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("company_agent_runtime_policies", "telegram_hmac_secret")
