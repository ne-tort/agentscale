"""Use session-local identity for durable detached-turn idempotency."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026110103"
down_revision = "2026110102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Replace the initial global client-id constraint with the API's
    # session-local idempotency identity: (session_id, turn_id).
    op.drop_constraint("uq_agent_turn_id", "agent_turns", type_="unique")
    op.create_unique_constraint(
        "uq_agent_turn_session_turn_id", "agent_turns", ["session_id", "turn_id"]
    )


def downgrade() -> None:
    # A scoped deployment may have accepted the same id in multiple sessions.
    # Keep the earliest reservation before restoring the historical global key.
    op.execute(
        sa.text(
            """
            DELETE FROM agent_turns AS later
            USING agent_turns AS earlier
            WHERE later.turn_id = earlier.turn_id
              AND (later.created_at, later.id) > (earlier.created_at, earlier.id)
            """
        )
    )
    op.drop_constraint(
        "uq_agent_turn_session_turn_id", "agent_turns", type_="unique"
    )
    op.create_unique_constraint("uq_agent_turn_id", "agent_turns", ["turn_id"])
