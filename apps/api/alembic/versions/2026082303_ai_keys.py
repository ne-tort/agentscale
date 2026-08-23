"""AI keys schema — AiProviderKey + CompanyAiKeyBinding.

Revision ID: ai_keys_001
Revises: identity_001
Create Date: 2026-08-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ai_keys_001"
down_revision: str | None = "identity_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_provider_keys",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("api_kind", sa.String(length=64), nullable=False),
        sa.Column("secret_ref", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("next_renewal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("renewal_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_ai_provider_keys_provider_status", "ai_provider_keys", ["provider", "status"])
    op.create_index("ix_ai_provider_keys_next_renewal", "ai_provider_keys", ["next_renewal_at"])

    op.create_table(
        "company_ai_key_bindings",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("company_id", sa.String(length=40), nullable=False),
        sa.Column("key_id", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["key_id"], ["ai_provider_keys.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("company_id", "key_id", name="uq_company_ai_key"),
    )


def downgrade() -> None:
    op.drop_table("company_ai_key_bindings")
    op.drop_index("ix_ai_provider_keys_next_renewal", table_name="ai_provider_keys")
    op.drop_index("ix_ai_provider_keys_provider_status", table_name="ai_provider_keys")
    op.drop_table("ai_provider_keys")
