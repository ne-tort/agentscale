"""Stub bootstrap schema — replace with product migrations from docs/target/.

Revision ID: stub_bootstrap
Revises:
Create Date: 2026-08-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "stub_bootstrap"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stub_meta",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )
    op.execute(sa.text("INSERT INTO stub_meta (key, value) VALUES ('bootstrap', '1')"))


def downgrade() -> None:
    op.drop_table("stub_meta")
