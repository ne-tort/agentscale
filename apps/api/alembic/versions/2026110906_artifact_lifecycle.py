"""Add the append-only plugin artifact publication lifecycle journal.

Revision ID: 2026110906
Revises: 2026110905

Additive only: create the lifecycle table and its history index. There is no
backfill, status/current update, trigger, or mutation of revisions 0901-0905.
Existing artifact/grant/instance records are not activated or rewritten.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2026110906"
down_revision: str | Sequence[str] | None = "2026110905"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "plugin_artifact_lifecycle"
_HISTORY_INDEX = "ix_plugin_artifact_lifecycle_artifact_occurred_at"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=80), primary_key=True, nullable=False),
        sa.Column("artifact_id", sa.String(length=80), nullable=False),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("actor", sa.String(length=160), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("operation_token", sa.String(length=128), nullable=False),
        sa.UniqueConstraint(
            "artifact_id",
            "operation_token",
            name="uq_plugin_artifact_lifecycle_artifact_operation_token",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["plugin_artifacts.id"],
            name="fk_plugin_artifact_lifecycle_artifact",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "operation IN ('publish', 'promote')",
            name="ck_plugin_artifact_lifecycle_operation",
        ),
        sa.CheckConstraint(
            "btrim(actor) <> ''",
            name="ck_plugin_artifact_lifecycle_actor_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(operation_token) <> ''",
            name="ck_plugin_artifact_lifecycle_operation_token_nonempty",
        ),
    )
    op.create_index(
        _HISTORY_INDEX,
        _TABLE,
        ["artifact_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index(_HISTORY_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)
