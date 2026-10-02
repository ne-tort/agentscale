"""Chat error-retry policy: projects.chat_error_policy JSONB.

Per-project reconnect settings for provider errors in the chat (model
unavailable / 5xx / rate limit / timeout):
    {"interval_sec": 10, "max_attempts": 0, "fallback_models": [...]}
- interval_sec  — wait between reconnect attempts (bridge turns it into ms);
- max_attempts  — reconnect budget; 0 = unlimited (the default);
- fallback_models — model ids the run rotates to per attempt (chosen in the
  UI from the project's live model list — same key-filtered list as the
  session model picker).

NULL column = server default policy (10s interval, unlimited attempts, no
fallback models) — nothing to backfill.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "2026100202"
down_revision = "2026100201"


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("chat_error_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "chat_error_policy")
