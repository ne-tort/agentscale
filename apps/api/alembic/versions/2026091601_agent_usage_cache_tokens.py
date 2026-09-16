"""Add cache tokens, message_id, token_source to agent_usage (audit CLAW-P0b).

Reason: token counting previously dropped Claude cache_creation/cache_read
tokens and had no dedupe key, so retried usage events could double-count
and prompt-cache savings were invisible to budget/metrics.

Columns are nullable for backward compatibility with existing rows; the
ORM and TokenNormalizer populate them going forward.
"""

import sqlalchemy as sa
from alembic import op

revision = "2026091601"
down_revision = "2026091501"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_usage",
        sa.Column("cache_creation_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "agent_usage",
        sa.Column("cache_read_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "agent_usage",
        sa.Column("message_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "agent_usage",
        sa.Column("token_source", sa.String(length=32), nullable=True),
    )
    op.create_index(
        "ix_agent_usage_session_message_id",
        "agent_usage",
        ["session_id", "message_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_usage_session_message_id",
        table_name="agent_usage",
    )
    op.drop_column("agent_usage", "token_source")
    op.drop_column("agent_usage", "message_id")
    op.drop_column("agent_usage", "cache_read_tokens")
    op.drop_column("agent_usage", "cache_creation_tokens")
