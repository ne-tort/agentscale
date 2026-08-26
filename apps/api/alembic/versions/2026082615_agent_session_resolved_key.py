"""agent_sessions.resolved_key_id for key disable cascade."""

import sqlalchemy as sa

from alembic import op

revision = "2026082615"
down_revision = "2026082323"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_sessions",
        sa.Column("resolved_key_id", sa.String(length=40), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_sessions_resolved_key_id",
        "agent_sessions",
        "ai_provider_keys",
        ["resolved_key_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_agent_sessions_resolved_key_id",
        "agent_sessions",
        ["resolved_key_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_sessions_resolved_key_id", table_name="agent_sessions")
    op.drop_constraint("fk_agent_sessions_resolved_key_id", "agent_sessions", type_="foreignkey")
    op.drop_column("agent_sessions", "resolved_key_id")
