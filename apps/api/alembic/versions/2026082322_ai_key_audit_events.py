"""Register ai_key_audit_events in Alembic (table already existed via runtime CREATE IF NOT EXISTS)."""

from alembic import op

revision = "2026082322"
down_revision = "2026082321"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: shared/dev may already have the table from AiKeyAuditService._ensure_table.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_key_audit_events (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            key_id TEXT,
            actor_sub TEXT,
            detail JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.drop_table("ai_key_audit_events")
