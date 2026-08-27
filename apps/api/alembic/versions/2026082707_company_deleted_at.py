"""Add companies.deleted_at for soft-delete + async cascade."""

import sqlalchemy as sa
from alembic import op

revision = "2026082707"
down_revision = "2026082706"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_companies_deleted_at", "companies", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_companies_deleted_at", table_name="companies")
    op.drop_column("companies", "deleted_at")
