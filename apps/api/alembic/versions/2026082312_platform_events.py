"""Add platform_events bus (L07) — company/project lifecycle, not project_triggers."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "2026082312"
down_revision = "2026082311"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_events",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("company_id", sa.String(length=40), nullable=True),
        sa.Column("project_id", sa.String(length=40), nullable=True),
        sa.Column("cabinet_id", sa.String(length=40), nullable=True),
        sa.Column("actor_sub", sa.String(length=120), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_platform_events_created", "platform_events", ["created_at"])
    op.create_index("ix_platform_events_company", "platform_events", ["company_id"])
    op.create_index("ix_platform_events_type", "platform_events", ["event_type"])


def downgrade() -> None:
    op.drop_index("ix_platform_events_type", table_name="platform_events")
    op.drop_index("ix_platform_events_company", table_name="platform_events")
    op.drop_index("ix_platform_events_created", table_name="platform_events")
    op.drop_table("platform_events")
