"""Merge ca-* duplicate models into canonical originals (explicit mapping).

ca-* models are duplicate aliases from cheapai.lol that mirror real models
already in the catalog. This migration moves each ca-* id into the original
model's key_aliases, re-points ai_key_model_bindings, and deletes the ca-*
catalog row + its SDK bindings. Explicit one-to-one mapping (no heuristics).
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "2026092102"
down_revision = "2026092101"
branch_labels = None
depends_on = None

# ca-* duplicate → canonical original name (already in catalog).
_CA_MAPPING: dict[str, str] = {
    "ca-6-astra": "gpt-6-astra",
    "ca-opus-4.6": "claude-opus-4.6",
    "ca-opus-4.7": "claude-opus-4.7",
    "ca-opus-4.8": "claude-opus-4.8",
    "ca-opus-5": "claude-opus-5",
    "ca-5.6-sol": "gpt-5.6-sol",
    "ca-5.6-terra": "gpt-5.6-terra",
    "ca-5.6-luna": "gpt-5.6-luna",
    "ca-5.5": "gpt-5.5",
    "ca-5.4": "gpt-5.4",
    "ca-5.4-mini": "gpt-5.4-mini",
    "ca-fable-5": "claude-fable-5",
    "ca-5.3": "glm-5.3",
    "ca-fable-5.1": "claude-fable-5.1",
    "ca-5.3-flash": "glm-5.3-flash",
    "ca-2.5-fast": "composer-2.5-fast",
    "ca-k2.7-code": "kimi-k2.7-code",
    "ca-v2.5": "mimo-v2.5",
    "ca-sonnet-5": "claude-sonnet-5",
    "ca-5-turbo": "glm-5-turbo",
    "ca-3.8-max": "qwen3.8-max",
    "ca-v4-pro": "deepseek-v4-pro",
    "ca-3.1-pro": "gemini-3.1-pro",
    "ca-sonnet-4.6": "claude-sonnet-4.6",
    "ca-5.2": "glm-5.2",
    "ca-3.7-max": "qwen3.7-max",
    "ca-v4-flash": "deepseek-v4-flash",
    "ca-3.5-flash": "gemini-3.5-flash",
    "ca-haiku-4.5": "claude-haiku-4.5",
    "ca-4.6": "grok-4.6",
    "ca-3.7-plus": "qwen3.7-plus",
    "ca-m3": "minimax-m3",
    "ca-3.6-flash": "gemini-3.6-flash",
    "ca-4.5": "grok-4.5",
    "ca-k3": "kimi-k3",
    "ca-v2.5-pro": "mimo-v2.5-pro",
    "ca-3.7-flash": "gemini-3.7-flash",
}


def upgrade() -> None:
    conn = op.get_bind()

    for ca_name, orig_name in _CA_MAPPING.items():
        ca = conn.execute(
            sa.text(
                "SELECT id FROM ai_models WHERE name = :n AND owner_scope = 'platform'"
            ),
            {"n": ca_name},
        ).fetchone()
        if ca is None:
            continue
        ca_id = ca[0]
        orig = conn.execute(
            sa.text(
                "SELECT id, key_aliases FROM ai_models WHERE name = :n AND owner_scope = 'platform'"
            ),
            {"n": orig_name},
        ).fetchone()
        if orig is None:
            # Original not present — skip; leave the ca-* row.
            continue
        orig_id, orig_aliases = orig
        aliases = orig_aliases if isinstance(orig_aliases, list) else []
        if ca_name not in [str(a) for a in aliases]:
            aliases = list(aliases) + [ca_name]
            conn.execute(
                sa.text("UPDATE ai_models SET key_aliases = :aliases WHERE id = :id"),
                {"aliases": json.dumps(aliases), "id": orig_id},
            )
        # Re-point bindings: if the key already binds the original, drop the
        # ca-* binding; else move it to the original.
        dup_bindings = conn.execute(
            sa.text(
                "SELECT id, key_id FROM ai_key_model_bindings WHERE model_id = :caid"
            ),
            {"caid": ca_id},
        ).fetchall()
        for bid, key_id in dup_bindings:
            existing = conn.execute(
                sa.text(
                    "SELECT id FROM ai_key_model_bindings WHERE key_id = :kid AND model_id = :oid"
                ),
                {"kid": key_id, "oid": orig_id},
            ).fetchone()
            if existing is None:
                conn.execute(
                    sa.text(
                        "UPDATE ai_key_model_bindings SET model_id = :oid WHERE id = :bid"
                    ),
                    {"oid": orig_id, "bid": bid},
                )
            else:
                conn.execute(sa.text("DELETE FROM ai_key_model_bindings WHERE id = :bid"), {"bid": bid})
        conn.execute(sa.text("DELETE FROM ai_model_sdk_bindings WHERE model_id = :caid"), {"caid": ca_id})
        conn.execute(sa.text("DELETE FROM ai_models WHERE id = :caid"), {"caid": ca_id})


def downgrade() -> None:
    pass
