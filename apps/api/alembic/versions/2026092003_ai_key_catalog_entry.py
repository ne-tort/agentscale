"""Add catalog_entry_id to ai_provider_keys for explicit endpoint binding (PROBE-P3).

Without this, ProviderResolver matches api_kind+agent_provider against the
ai.http_providers catalog, which is ambiguous for `custom` api_kind (multiple
custom+codex entries: ollama + any user-added OpenAI-compatible endpoint).
This made custom/HTTP keys probe the wrong endpoint (e.g. a cheapai.lol key
probed localhost ollama → PROBE_NETWORK → UI "Проверка недоступна").

catalog_entry_id stores the explicit catalog entry id (reference_catalog_entries.id
within catalog 'ai.http_providers') the key was created/edited against. The
resolver prefers it before falling back to the legacy api_kind+agent_provider match.
"""

from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "2026092003"
down_revision = "2026092002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        text(
            "ALTER TABLE ai_provider_keys "
            "ADD COLUMN IF NOT EXISTS catalog_entry_id VARCHAR(64)"
        )
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_ai_provider_keys_catalog_entry_id "
            "ON ai_provider_keys (catalog_entry_id)"
        )
    )
    # Backfill best-effort from current provider/api_kind → catalog match.
    # Only unambiguous cases (exactly one non-archived catalog entry matches
    # api_kind+agent_provider). Ambiguous (custom+codex: ollama vs cheapai)
    # stay NULL and resolve at probe time via the new explicit path only when
    # the user picks an endpoint in the UI.
    op.execute(
        text(
            """
            UPDATE ai_provider_keys k
            SET catalog_entry_id = sub.id
            FROM (
                SELECT e.id, e.payload->>'api_kind' AS api_kind,
                       e.payload->>'agent_provider' AS agent_provider
                FROM reference_catalog_entries e
                WHERE e.catalog_id = 'ai.http_providers'
                  AND e.archived_at IS NULL
            ) AS sub
            WHERE k.catalog_entry_id IS NULL
              AND k.api_kind = sub.api_kind
              AND k.provider = sub.agent_provider
              AND (
                  SELECT COUNT(*) FROM reference_catalog_entries e2
                  WHERE e2.catalog_id = 'ai.http_providers'
                    AND e2.archived_at IS NULL
                    AND e2.payload->>'api_kind' = k.api_kind
                    AND e2.payload->>'agent_provider' = k.provider
              ) = 1
            """
        )
    )


def downgrade() -> None:
    op.execute(
        text(
            "DROP INDEX IF EXISTS ix_ai_provider_keys_catalog_entry_id"
        )
    )
    op.execute(
        text("ALTER TABLE ai_provider_keys DROP COLUMN IF EXISTS catalog_entry_id")
    )
