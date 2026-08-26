"""Company contact email + phone."""

import sqlalchemy as sa

from alembic import op

revision = "2026082323"
down_revision = "2026082322"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("contact_email", sa.String(length=320), nullable=True),
    )
    op.add_column(
        "companies",
        sa.Column("phone", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("companies", "phone")
    op.drop_column("companies", "contact_email")
