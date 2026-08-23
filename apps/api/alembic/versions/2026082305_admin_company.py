"""Company cabinet quotas and agent runtime policy (L04).

Revision ID: admin_company_001
Revises: cabinets_001
Create Date: 2026-08-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "admin_company_001"
down_revision: str | None = "cabinets_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "company_cabinet_quotas",
        sa.Column("company_id", sa.String(length=40), primary_key=True),
        sa.Column("max_cabinets", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("max_packages_per_cabinet", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("max_bundle_import_mb", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "company_agent_runtime_policies",
        sa.Column("company_id", sa.String(length=40), primary_key=True),
        sa.Column("tool_preset", sa.String(length=64), nullable=False, server_default="workspace_dev"),
        sa.Column("preferred_provider", sa.String(length=32), nullable=True),
        sa.Column("platform_fallback", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("model_allowlist", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("company_agent_runtime_policies")
    op.drop_table("company_cabinet_quotas")
