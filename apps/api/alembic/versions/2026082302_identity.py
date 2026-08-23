"""Identity schema — Company / Employee / Membership.

Revision ID: identity_001
Revises: stub_bootstrap
Create Date: 2026-08-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "identity_001"
down_revision: str | None = "stub_bootstrap"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_table(
        "employees",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("keycloak_sub", sa.String(length=120), nullable=True, unique=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_employees_email", "employees", ["email"])
    op.create_table(
        "memberships",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("company_id", sa.String(length=40), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("employee_id", sa.String(length=40), sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("company_id", "employee_id", name="uq_membership_company_employee"),
    )


def downgrade() -> None:
    op.drop_table("memberships")
    op.drop_index("ix_employees_email", table_name="employees")
    op.drop_table("employees")
    op.drop_table("companies")
