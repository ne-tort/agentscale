"""Partial unique indexes — allow reusing names/slugs of soft-deleted rows."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026082909"
down_revision = "2026082908"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_project_cabinet_slug", "projects", type_="unique")
    op.create_index(
        "uq_project_cabinet_slug_alive",
        "projects",
        ["cabinet_id", "slug"],
        unique=True,
        postgresql_where=sa.text("status != 'deleted'"),
    )

    op.drop_index("ix_employees_login", table_name="employees")
    op.create_index(
        "ix_employees_login_alive",
        "employees",
        ["login"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.drop_constraint("uq_reference_catalog_entry", "reference_catalog_entries", type_="unique")
    op.create_index(
        "uq_reference_catalog_entry_alive",
        "reference_catalog_entries",
        ["catalog_id", "id"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_reference_catalog_entry_alive", table_name="reference_catalog_entries")
    op.create_unique_constraint(
        "uq_reference_catalog_entry",
        "reference_catalog_entries",
        ["catalog_id", "id"],
    )

    op.drop_index("ix_employees_login_alive", table_name="employees")
    op.create_index("ix_employees_login", "employees", ["login"], unique=True)

    op.drop_index("uq_project_cabinet_slug_alive", table_name="projects")
    op.create_unique_constraint("uq_project_cabinet_slug", "projects", ["cabinet_id", "slug"])
