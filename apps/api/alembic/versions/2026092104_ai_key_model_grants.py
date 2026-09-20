"""AI key model grants — per-delegation cascade narrowing (MODELS-L3).

Introduces ai_key_model_grants: per-delegation model grants with cascade
narrowing. A key owner (platform or company) issues owner-level grants on the
key itself (the ceiling); downstream delegates (company / employee / project /
cabinet) receive their own grants that can only narrow the ceiling, never
widen it. One key delegated to several companies can carry an individual model
set per company.

Backfill: existing ai_key_model_bindings rows are lifted to owner-level grants
(delegate_kind="key") preserving enabled/is_default, with granted_by_scope
inferred from the key's owner_scope. The legacy ai_key_model_bindings table
stays as the runtime-materialized effective state and is kept in sync by the
service layer (so policy_service and the probe path are unaffected).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "2026092104"
down_revision = "2026092103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_key_model_grants",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column(
            "key_id",
            sa.String(length=40),
            sa.ForeignKey("ai_provider_keys.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "model_id",
            sa.String(length=40),
            sa.ForeignKey("ai_models.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("granted_by_scope", sa.String(length=32), nullable=False, server_default="platform"),
        sa.Column(
            "granted_by_company_id",
            sa.String(length=40),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("delegate_kind", sa.String(length=32), nullable=False, server_default="key"),
        sa.Column("delegate_id", sa.String(length=40), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint(
            "key_id",
            "model_id",
            "granted_by_scope",
            "granted_by_company_id",
            "delegate_kind",
            "delegate_id",
            name="uq_ai_key_model_grant",
        ),
    )
    op.create_index(
        "ix_ai_key_model_grants_key_delegate",
        "ai_key_model_grants",
        ["key_id", "delegate_kind", "delegate_id"],
    )

    # Backfill owner-level grants from existing effective bindings. The grant's
    # authority mirrors the key owner: a platform-owned key → platform grants;
    # a company-owned key → company grants (granted_by_company_id = owner).
    op.execute(
        sa.text(
            """
            INSERT INTO ai_key_model_grants
                (id, key_id, model_id, granted_by_scope, granted_by_company_id,
                 delegate_kind, delegate_id, enabled, is_default, created_at)
            SELECT
                'kmg_' || b.id,
                b.key_id,
                b.model_id,
                COALESCE(k.owner_scope, 'platform'),
                CASE WHEN k.owner_scope = 'company' THEN k.owner_company_id ELSE NULL END,
                'key',
                NULL,
                b.enabled,
                b.is_default,
                b.created_at
            FROM ai_key_model_bindings b
            JOIN ai_provider_keys k ON k.id = b.key_id
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_ai_key_model_grants_key_delegate", table_name="ai_key_model_grants")
    op.drop_table("ai_key_model_grants")
