"""Add redacted rollback command fingerprints to artifact lifecycle.

Revision ID: 2026110908
Revises: 2026110907

Additive nullable column only. There is no backfill: historical rollback rows
without command_fingerprint are not accepted as exact replay evidence.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2026110908"
down_revision: str | Sequence[str] | None = "2026110907"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "plugin_artifact_lifecycle"
_COLUMN = "command_fingerprint"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(_COLUMN, sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column(_TABLE, _COLUMN)
