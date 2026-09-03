"""Multi-chat: session title/last_message_at + employee selection + pins."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026090302"
down_revision = "2026090301"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_sessions", sa.Column("title", sa.String(length=200), nullable=True))
    op.add_column(
        "agent_sessions",
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_sessions_last_message_at", "agent_sessions", ["last_message_at"])

    op.create_table(
        "employee_project_selections",
        sa.Column("employee_id", sa.String(length=40), nullable=False),
        sa.Column("cabinet_id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cabinet_id"], ["cabinet_instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("employee_id", "cabinet_id"),
    )

    op.create_table(
        "employee_chat_pins",
        sa.Column("employee_id", sa.String(length=40), nullable=False),
        sa.Column("session_id", sa.String(length=40), nullable=False),
        sa.Column("pinned_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("employee_id", "session_id"),
    )


def downgrade() -> None:
    op.drop_table("employee_chat_pins")
    op.drop_table("employee_project_selections")
    op.drop_index("ix_agent_sessions_last_message_at", table_name="agent_sessions")
    op.drop_column("agent_sessions", "last_message_at")
    op.drop_column("agent_sessions", "title")
