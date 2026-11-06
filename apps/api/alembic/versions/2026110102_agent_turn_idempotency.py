"""Durable idempotency ledger for detached chat turns."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026110102"
down_revision = "2026110101"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_turns",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("session_id", sa.String(length=40), nullable=False),
        sa.Column("turn_id", sa.String(length=64), nullable=False),
        sa.Column("start_seq", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="started"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("turn_id", name="uq_agent_turn_id"),
    )
    op.create_index("ix_agent_turns_session", "agent_turns", ["session_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_turns_session", table_name="agent_turns")
    op.drop_table("agent_turns")
