"""Lifecycle columns: employees.deleted_at, companies.status (active|paused)."""

import sqlalchemy as sa
from alembic import op

revision = "2026082708"
down_revision = "2026082707"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "employees",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_employees_deleted_at", "employees", ["deleted_at"])
    op.add_column(
        "companies",
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
    )


def downgrade() -> None:
    op.drop_column("companies", "status")
    op.drop_index("ix_employees_deleted_at", table_name="employees")
    op.drop_column("employees", "deleted_at")
