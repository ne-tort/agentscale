"""Agent budget columns on company policy (L04/L08).

Revision ID: agent_budget_001
Revises: agent_001
Create Date: 2026-08-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "agent_budget_001"
down_revision: str | None = "agent_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "company_agent_runtime_policies",
        sa.Column("max_agent_tokens_month", sa.Integer(), nullable=True),
    )
    op.add_column(
        "company_agent_runtime_policies",
        sa.Column("max_tokens_per_run", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("company_agent_runtime_policies", "max_tokens_per_run")
    op.drop_column("company_agent_runtime_policies", "max_agent_tokens_month")
