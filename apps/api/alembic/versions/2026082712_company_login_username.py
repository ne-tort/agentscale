"""Company login_username for editable org login."""

import sqlalchemy as sa
from alembic import op

revision = "2026082712"
down_revision = "2026082711"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("login_username", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_companies_login_username", "companies", ["login_username"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_companies_login_username", table_name="companies")
    op.drop_column("companies", "login_username")
