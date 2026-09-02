"""AI model catalog + SDK/key bindings."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026090201"
down_revision = "2026082910"
branch_labels = None
depends_on = None

_CURSOR_MODELS = (
    "default",
    "grok-4.6",
    "composer-2.5",
    "claude-opus-5",
    "claude-opus-4-8",
    "gpt-5.6-sol",
    "gpt-5.5",
    "claude-fable-5-1",
    "claude-fable-5",
    "grok-4.5",
    "gemini-3.7-flash",
    "gpt-5.6-terra",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "composer-2",
    "gpt-5.3-codex",
    "claude-opus-4-7",
    "gpt-5.4",
    "claude-opus-4-6",
    "claude-opus-4-5",
    "gpt-5.2",
    "gpt-5.6-luna",
    "gemini-3.6-flash",
    "gemini-3.1-pro",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "claude-haiku-4-5",
    "claude-sonnet-4-5",
    "gpt-5.1",
    "gemini-3.5-flash",
    "claude-sonnet-4",
    "gpt-5-mini",
    "gemini-2.5-flash",
    "kimi-k3",
    "kimi-k2.7-code",
    "glm-5.2",
    "gemini-3-flash",
)

_CODEX_MODELS = ("gpt-5.3-codex", "gpt-5.2", "gpt-5.1", "gpt-5-mini")
_CLAUDE_MODELS = ("claude-sonnet-4-6", "claude-opus-4-8", "claude-haiku-4-5", "claude-sonnet-4-5")


def upgrade() -> None:
    op.create_table(
        "ai_models",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("owner_scope", sa.String(length=32), nullable=False, server_default="platform"),
        sa.Column("owner_company_id", sa.String(length=40), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_table(
        "ai_model_sdk_bindings",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("model_id", sa.String(length=40), sa.ForeignKey("ai_models.id", ondelete="CASCADE"), nullable=False),
        sa.Column("api_kind", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("model_id", "api_kind", name="uq_ai_model_sdk"),
    )
    op.create_table(
        "ai_key_model_bindings",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("key_id", sa.String(length=40), sa.ForeignKey("ai_provider_keys.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_id", sa.String(length=40), sa.ForeignKey("ai_models.id", ondelete="CASCADE"), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("key_id", "model_id", name="uq_ai_key_model"),
    )

    conn = op.get_bind()
    for idx, name in enumerate(_CURSOR_MODELS):
        model_id = f"mdl_seed_cursor_{idx:03d}"
        conn.execute(
            sa.text(
                "INSERT INTO ai_models (id, name, owner_scope) VALUES (:id, :name, 'platform')"
            ),
            {"id": model_id, "name": name},
        )
        conn.execute(
            sa.text(
                "INSERT INTO ai_model_sdk_bindings (id, model_id, api_kind) "
                "VALUES (:bid, :mid, 'cursor_sdk')"
            ),
            {"bid": f"msb_seed_cursor_{idx:03d}", "mid": model_id},
        )
    for idx, name in enumerate(_CODEX_MODELS):
        model_id = f"mdl_seed_codex_{idx:03d}"
        conn.execute(
            sa.text("INSERT INTO ai_models (id, name, owner_scope) VALUES (:id, :name, 'platform')"),
            {"id": model_id, "name": name},
        )
        conn.execute(
            sa.text(
                "INSERT INTO ai_model_sdk_bindings (id, model_id, api_kind) "
                "VALUES (:bid, :mid, 'codex_sdk')"
            ),
            {"bid": f"msb_seed_codex_{idx:03d}", "mid": model_id},
        )
    for idx, name in enumerate(_CLAUDE_MODELS):
        model_id = f"mdl_seed_claude_{idx:03d}"
        conn.execute(
            sa.text("INSERT INTO ai_models (id, name, owner_scope) VALUES (:id, :name, 'platform')"),
            {"id": model_id, "name": name},
        )
        conn.execute(
            sa.text(
                "INSERT INTO ai_model_sdk_bindings (id, model_id, api_kind) "
                "VALUES (:bid, :mid, 'claude_agent_sdk')"
            ),
            {"bid": f"msb_seed_claude_{idx:03d}", "mid": model_id},
        )


def downgrade() -> None:
    op.drop_table("ai_key_model_bindings")
    op.drop_table("ai_model_sdk_bindings")
    op.drop_table("ai_models")
