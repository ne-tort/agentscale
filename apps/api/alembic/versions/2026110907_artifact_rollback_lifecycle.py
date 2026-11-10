"""Allow durable artifact rollback lifecycle entries.

Revision ID: 2026110907
Revises: 2026110906

Additive operation vocabulary change only. Existing publish/promote rows and
artifact/current state are not rewritten or activated.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2026110907"
down_revision: str | Sequence[str] | None = "2026110906"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "plugin_artifact_lifecycle"
_CONSTRAINT = "ck_plugin_artifact_lifecycle_operation"
_ROLLBACK_EXISTS = sa.text(
    "SELECT EXISTS ("
    "SELECT 1 FROM plugin_artifact_lifecycle WHERE operation = 'rollback'"
    ")"
)


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        "operation IN ('publish', 'promote', 'rollback')",
    )


def downgrade() -> None:
    rollback_exists = op.get_bind().execute(_ROLLBACK_EXISTS).scalar_one()
    if rollback_exists:
        raise RuntimeError(
            "2026110907 downgrade refused: rollback lifecycle rows exist"
        )

    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        "operation IN ('publish', 'promote')",
    )
