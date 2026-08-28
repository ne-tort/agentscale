"""Drop companies.login_username — org login is always company id."""

from alembic import op
import sqlalchemy as sa

revision = "2026082715"
down_revision = "2026082714"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_companies_login_username", table_name="companies")
    op.drop_column("companies", "login_username")


def downgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("login_username", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_companies_login_username", "companies", ["login_username"], unique=True)
