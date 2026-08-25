"""Add optional company description (L04)."""

from alembic import op
import sqlalchemy as sa

revision = "2026082318"
down_revision = "2026082317"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("description", sa.String(length=2000), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("companies", "description")
