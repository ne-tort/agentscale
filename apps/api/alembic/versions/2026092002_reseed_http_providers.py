"""Re-seed ai.http_providers catalog: fix openai /v1 doubling, add cursor_dashboard + cursor_workos (MODELS-L2)."""

from __future__ import annotations

import json

from sqlalchemy import text

from alembic import op

revision = "2026092002"
down_revision = "2026092001"
branch_labels = None
depends_on = None


_SEED = [
    {
        "id": "openai",
        "title": "OpenAI",
        "subtitle": "api.openai.com",
        "icon_name": "smart_toy_outlined",
        "sort_order": 10,
        "payload": {
            "api_kind": "openai_api",
            "agent_provider": "codex",
            "base_url": "https://api.openai.com",
            "openai_compatible": True,
            "auth_scheme": "bearer",
            "chat_completions_path": "/v1/chat/completions",
            "models_path": "/v1/models",
            "supports_models_list": True,
        },
    },
    {
        "id": "anthropic",
        "title": "Anthropic",
        "subtitle": "api.anthropic.com",
        "icon_name": "psychology_outlined",
        "sort_order": 20,
        "payload": {
            "api_kind": "anthropic_api",
            "agent_provider": "claude_code",
            "base_url": "https://api.anthropic.com",
            "openai_compatible": False,
            "auth_scheme": "x-api-key",
            "chat_completions_path": "/v1/messages",
            "models_path": "/v1/models",
            "supports_models_list": True,
        },
    },
    {
        "id": "openrouter",
        "title": "OpenRouter",
        "subtitle": "openrouter.ai",
        "icon_name": "hub_outlined",
        "sort_order": 30,
        "payload": {
            "api_kind": "openrouter",
            "agent_provider": "codex",
            "base_url": "https://openrouter.ai/api/v1",
            "openai_compatible": True,
            "auth_scheme": "bearer",
            "chat_completions_path": "/chat/completions",
            "models_path": "/models",
            "supports_models_list": True,
        },
    },
    {
        "id": "cursor",
        "title": "Cursor (Dashboard API)",
        "subtitle": "api.cursor.com",
        "icon_name": "terminal_outlined",
        "sort_order": 40,
        "payload": {
            "api_kind": "custom",
            "agent_provider": "cursor",
            "base_url": "https://api.cursor.com",
            "openai_compatible": True,
            "auth_scheme": "bearer",
            "chat_completions_path": "/v1/chat/completions",
            "models_path": "/v1/models",
            "supports_models_list": True,
        },
    },
    {
        "id": "cursor_workos",
        "title": "Cursor (WorkOS token)",
        "subtitle": "api2.cursor.sh",
        "icon_name": "terminal_outlined",
        "sort_order": 41,
        "payload": {
            "api_kind": "custom",
            "agent_provider": "cursor",
            "base_url": "https://api2.cursor.sh",
            "openai_compatible": True,
            "auth_scheme": "bearer",
            "chat_completions_path": "/v1/chat/completions",
            "models_path": "/v1/models",
            "supports_models_list": False,
        },
    },
    {
        "id": "ollama",
        "title": "Ollama",
        "subtitle": "localhost:11434",
        "icon_name": "smart_toy_outlined",
        "sort_order": 50,
        "payload": {
            "api_kind": "custom",
            "agent_provider": "codex",
            "base_url": "http://127.0.0.1:11434/v1",
            "openai_compatible": True,
            "auth_scheme": "none",
            "chat_completions_path": "/chat/completions",
            "models_path": "/models",
            "supports_models_list": True,
        },
    },
]


def upgrade() -> None:
    conn = op.get_bind()
    for spec in _SEED:
        payload_json = json.dumps(spec["payload"], ensure_ascii=False)
        conn.execute(
            text(
                """
                INSERT INTO reference_catalog_entries
                    (pk, catalog_id, id, title, subtitle, icon_name, payload, seeded, sort_order)
                VALUES
                    (:pk, 'ai.http_providers', :id, :title, :subtitle, :icon_name,
                     CAST(:payload AS jsonb), true, :sort_order)
                ON CONFLICT (catalog_id, id) WHERE archived_at IS NULL DO UPDATE SET
                    title = EXCLUDED.title,
                    subtitle = EXCLUDED.subtitle,
                    icon_name = EXCLUDED.icon_name,
                    payload = EXCLUDED.payload,
                    sort_order = EXCLUDED.sort_order,
                    seeded = true
                """
            ),
            {
                "pk": f"rce_seed_{spec['id']}",
                "id": spec["id"],
                "title": spec["title"],
                "subtitle": spec.get("subtitle"),
                "icon_name": spec.get("icon_name"),
                "payload": payload_json,
                "sort_order": spec.get("sort_order", 0),
            },
        )


def downgrade() -> None:
    # Best-effort: remove the new cursor_workos entry only.
    conn = op.get_bind()
    conn.execute(
        text(
            "DELETE FROM reference_catalog_entries "
            "WHERE catalog_id='ai.http_providers' AND id='cursor_workos'"
        )
    )
