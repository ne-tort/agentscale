"""Session lifecycle + workspace outdated flag + adapter_state."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "2026090210"
down_revision = "2026090201"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("workspace_outdated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_sessions",
        sa.Column("adapter_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_sessions", "adapter_state")
    op.drop_column("projects", "workspace_outdated_at")
