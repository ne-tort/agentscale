"""Agent runtime persistence.

Revision ID: agent_001
Revises: projects_001
Create Date: 2026-08-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "agent_001"
down_revision: str | None = "projects_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("api_kind", sa.String(length=32), nullable=False),
        sa.Column("vendor_agent_id", sa.String(length=128), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("cwd", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_agent_sessions_project", "agent_sessions", ["project_id"])

    op.create_table(
        "agent_events",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("session_id", sa.String(length=40), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("session_id", "seq", name="uq_agent_event_session_seq"),
    )

    op.create_table(
        "agent_usage",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("session_id", sa.String(length=40), nullable=False),
        sa.Column("turn_id", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_agent_usage_session", "agent_usage", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_usage_session", table_name="agent_usage")
    op.drop_table("agent_usage")
    op.drop_table("agent_events")
    op.drop_index("ix_agent_sessions_project", table_name="agent_sessions")
    op.drop_table("agent_sessions")
