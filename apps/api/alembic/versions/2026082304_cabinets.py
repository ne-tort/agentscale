"""Cabinet instance registry.

Revision ID: cabinets_001
Revises: ai_keys_001
Create Date: 2026-08-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "cabinets_001"
down_revision: str | None = "ai_keys_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cabinet_instances",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("schema_name", sa.String(length=64), nullable=False),
        sa.Column("owner_employee_id", sa.String(length=40), nullable=False),
        sa.Column("company_id", sa.String(length=40), nullable=False),
        sa.Column("base_template", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("schema_name", name="uq_cabinet_schema_name"),
    )
    op.create_index("ix_cabinet_instances_owner", "cabinet_instances", ["owner_employee_id"])
    op.create_index("ix_cabinet_instances_company", "cabinet_instances", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_cabinet_instances_company", table_name="cabinet_instances")
    op.drop_index("ix_cabinet_instances_owner", table_name="cabinet_instances")
    op.drop_table("cabinet_instances")
