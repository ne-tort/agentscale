"""xAI (Grok) как OAuth-провайдер: запись каталога ai.http_providers
(api.x.ai, OpenAI-совместимый, bearer, api_kind xai_oauth). Секрет ключа —
OAuth-токены (device-code flow), access_token обновляется сервером.
"""

from __future__ import annotations

import json

from sqlalchemy import text

from alembic import op

revision = "2026100509"
down_revision = "2026100508"
branch_labels = None
depends_on = None


_ENTRY = {
    "id": "xai",
    "title": "xAI (Grok)",
    "subtitle": "api.x.ai — SuperGrok OAuth",
    "icon_name": "bolt_outlined",
    "sort_order": 35,
    "payload": {
        "api_kind": "xai_oauth",
        "agent_provider": "xai",
        "base_url": "https://api.x.ai/v1",
        "openai_compatible": True,
        "auth_scheme": "bearer",
        "chat_completions_path": "/chat/completions",
        "models_path": "/models",
        "supports_models_list": True,
    },
}


def upgrade() -> None:
    conn = op.get_bind()
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
            "pk": f"rce_seed_{_ENTRY['id']}",
            "id": _ENTRY["id"],
            "title": _ENTRY["title"],
            "subtitle": _ENTRY.get("subtitle"),
            "icon_name": _ENTRY.get("icon_name"),
            "payload": json.dumps(_ENTRY["payload"], ensure_ascii=False),
            "sort_order": _ENTRY.get("sort_order", 0),
        },
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text(
            "DELETE FROM reference_catalog_entries "
            "WHERE catalog_id='ai.http_providers' AND id='xai'"
        )
    )
