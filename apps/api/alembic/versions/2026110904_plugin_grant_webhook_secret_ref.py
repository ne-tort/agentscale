"""Artifact-pinned opaque webhook secret references for plugin grants.

Revision ID: 2026110904
Revises: 2026110903
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2026110904"
down_revision: str | Sequence[str] | None = "2026110903"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "plugin_capability_grants"
_COLUMN = "webhook_secret_ref"


def upgrade() -> None:
    # Existing rows intentionally remain NULL: resolvers must fail closed rather
    # than fabricating a secret for grants that predate this lifecycle contract.
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(length=2048), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, _COLUMN)
