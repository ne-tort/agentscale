"""Add durable administrative plugin publication approvals.

Revision ID: 2026110902
Revises: 2026110901

Additive only: create one approval table and one lookup index. No grant/artifact
rows are updated, no status is backfilled, and historical installations keep
exactly their previous lifecycle. Pending 2026110802 is not loaded or changed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "2026110902"
down_revision: str | Sequence[str] | None = "2026110901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "plugin_publication_approvals"


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(80), primary_key=True, nullable=False),
        sa.Column("company_id", sa.String(40), nullable=False),
        sa.Column("plugin_id", sa.String(160), nullable=False),
        sa.Column("manifest_version", sa.String(128), nullable=False),
        sa.Column("artifact_digest", sa.String(71), nullable=False),
        sa.Column("capabilities", _jsonb(), nullable=False),
        sa.Column("egress_policy", _jsonb(), nullable=False),
        sa.Column("quota", _jsonb(), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "company_id",
            "plugin_id",
            "manifest_version",
            "artifact_digest",
            name="uq_plugin_publication_approval_binding",
        ),
        sa.CheckConstraint(
            "artifact_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_plugin_publication_approval_digest",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(capabilities) = 'array' AND jsonb_array_length(capabilities) > 0",
            name="ck_plugin_publication_approval_capabilities",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(egress_policy) = 'object'",
            name="ck_plugin_publication_approval_egress_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(quota) = 'object'",
            name="ck_plugin_publication_approval_quota_object",
        ),
        sa.CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= created_at",
            name="ck_plugin_publication_approval_consumed_after_created",
        ),
    )
    op.create_index(
        "ix_plugin_publication_approvals_company_plugin",
        _TABLE,
        ["company_id", "plugin_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_plugin_publication_approvals_company_plugin", table_name=_TABLE
    )
    op.drop_table(_TABLE)
