"""Backfill model catalog: aliases for seeds, merge ca-* duplicates, fill metadata.

- For seed models (created by 2026090201 with empty key_aliases): set
  key_aliases = [name] so alias-based probe matching works.
- Merge ca-* duplicate models into their canonical originals: move ca-* ids
  into the original's key_aliases, re-point ai_key_model_bindings, delete the
  ca-* catalog rows. Mapping: ca-X → the existing model whose name equals X
  (e.g. ca-3.5-flash → gemini-3.5-flash, ca-3.8-max → qwen3.8-max,
  ca-opus-4.8 → claude-opus-4.8, ca-k3 → kimi-k3).
- Fill publisher / input_price_usd_per_mtok / output_price_usd_per_mtok /
  max_context_tokens for known models (best-effort public pricing as of 2026).
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "2026092101"
down_revision = "2026092003"
branch_labels = None
depends_on = None


# Best-effort public pricing (USD per 1M tokens) + context windows + publisher.
# Sources: vendor pricing pages as of mid-2026. Rounded; not live quotes.
_MODEL_META: dict[str, dict] = {
    # Anthropic Claude
    "claude-opus-5": {"publisher": "Anthropic", "input": 25.0, "output": 125.0, "ctx": 200_000},
    "claude-opus-4-8": {"publisher": "Anthropic", "input": 15.0, "output": 75.0, "ctx": 200_000},
    "claude-opus-4-7": {"publisher": "Anthropic", "input": 15.0, "output": 75.0, "ctx": 200_000},
    "claude-opus-4-6": {"publisher": "Anthropic", "input": 15.0, "output": 75.0, "ctx": 200_000},
    "claude-opus-4-5": {"publisher": "Anthropic", "input": 15.0, "output": 75.0, "ctx": 200_000},
    "claude-opus-4.6": {"publisher": "Anthropic", "input": 15.0, "output": 75.0, "ctx": 200_000},
    "claude-opus-4.7": {"publisher": "Anthropic", "input": 15.0, "output": 75.0, "ctx": 200_000},
    "claude-opus-4.8": {"publisher": "Anthropic", "input": 15.0, "output": 75.0, "ctx": 200_000},
    "claude-sonnet-5": {"publisher": "Anthropic", "input": 3.0, "output": 15.0, "ctx": 200_000},
    "claude-sonnet-4-6": {"publisher": "Anthropic", "input": 3.0, "output": 15.0, "ctx": 200_000},
    "claude-sonnet-4-5": {"publisher": "Anthropic", "input": 3.0, "output": 15.0, "ctx": 200_000},
    "claude-sonnet-4": {"publisher": "Anthropic", "input": 3.0, "output": 15.0, "ctx": 200_000},
    "claude-sonnet-4.6": {"publisher": "Anthropic", "input": 3.0, "output": 15.0, "ctx": 200_000},
    "claude-haiku-4-5": {"publisher": "Anthropic", "input": 0.8, "output": 4.0, "ctx": 200_000},
    "claude-haiku-4.5": {"publisher": "Anthropic", "input": 0.8, "output": 4.0, "ctx": 200_000},
    "claude-fable-5": {"publisher": "Anthropic", "input": 1.0, "output": 5.0, "ctx": 200_000},
    "claude-fable-5-1": {"publisher": "Anthropic", "input": 1.0, "output": 5.0, "ctx": 200_000},
    "claude-fable-5.1": {"publisher": "Anthropic", "input": 1.0, "output": 5.0, "ctx": 200_000},
    # OpenAI GPT
    "gpt-5.6-sol": {"publisher": "OpenAI", "input": 1.25, "output": 10.0, "ctx": 400_000},
    "gpt-5.6-terra": {"publisher": "OpenAI", "input": 1.25, "output": 10.0, "ctx": 400_000},
    "gpt-5.6-luna": {"publisher": "OpenAI", "input": 1.25, "output": 10.0, "ctx": 400_000},
    "gpt-5.5": {"publisher": "OpenAI", "input": 1.25, "output": 10.0, "ctx": 400_000},
    "gpt-5.4": {"publisher": "OpenAI", "input": 1.25, "output": 10.0, "ctx": 400_000},
    "gpt-5.4-mini": {"publisher": "OpenAI", "input": 0.15, "output": 0.6, "ctx": 256_000},
    "gpt-5.4-nano": {"publisher": "OpenAI", "input": 0.05, "output": 0.2, "ctx": 256_000},
    "gpt-5.3-codex": {"publisher": "OpenAI", "input": 1.25, "output": 10.0, "ctx": 400_000},
    "gpt-5.2": {"publisher": "OpenAI", "input": 1.25, "output": 10.0, "ctx": 400_000},
    "gpt-5.1": {"publisher": "OpenAI", "input": 1.25, "output": 10.0, "ctx": 400_000},
    "gpt-5-mini": {"publisher": "OpenAI", "input": 0.15, "output": 0.6, "ctx": 256_000},
    "gpt-6-astra": {"publisher": "OpenAI", "input": 2.5, "output": 20.0, "ctx": 1_000_000},
    # Google Gemini
    "gemini-3.8-flash": {"publisher": "Google", "input": 0.15, "output": 0.6, "ctx": 1_000_000},
    "gemini-3.7-flash": {"publisher": "Google", "input": 0.15, "output": 0.6, "ctx": 1_000_000},
    "gemini-3.6-flash": {"publisher": "Google", "input": 0.15, "output": 0.6, "ctx": 1_000_000},
    "gemini-3.5-flash": {"publisher": "Google", "input": 0.15, "output": 0.6, "ctx": 1_000_000},
    "gemini-3-flash": {"publisher": "Google", "input": 0.15, "output": 0.6, "ctx": 1_000_000},
    "gemini-3.1-pro": {"publisher": "Google", "input": 1.25, "output": 5.0, "ctx": 2_000_000},
    "gemini-2.5-flash": {"publisher": "Google", "input": 0.15, "output": 0.6, "ctx": 1_000_000},
    # xAI Grok
    "grok-4.6": {"publisher": "xAI", "input": 3.0, "output": 15.0, "ctx": 1_000_000},
    "grok-4.5": {"publisher": "xAI", "input": 3.0, "output": 15.0, "ctx": 1_000_000},
    # OpenAI Composer
    "composer-2.5": {"publisher": "OpenAI", "input": 1.25, "output": 10.0, "ctx": 400_000},
    "composer-2.5-fast": {"publisher": "OpenAI", "input": 0.5, "output": 4.0, "ctx": 400_000},
    "composer-2": {"publisher": "OpenAI", "input": 1.25, "output": 10.0, "ctx": 400_000},
    # Moonshot Kimi
    "kimi-k3": {"publisher": "Moonshot", "input": 0.6, "output": 2.5, "ctx": 256_000},
    "kimi-k2.7-code": {"publisher": "Moonshot", "input": 0.6, "output": 2.5, "ctx": 256_000},
    # Zhipu GLM
    "glm-5.3": {"publisher": "Zhipu", "input": 0.5, "output": 2.0, "ctx": 128_000},
    "glm-5.3-flash": {"publisher": "Zhipu", "input": 0.1, "output": 0.4, "ctx": 128_000},
    "glm-5-turbo": {"publisher": "Zhipu", "input": 0.5, "output": 2.0, "ctx": 128_000},
    "glm-5.2": {"publisher": "Zhipu", "input": 0.5, "output": 2.0, "ctx": 128_000},
    # Alibaba Qwen
    "qwen3.8-max": {"publisher": "Alibaba", "input": 0.5, "output": 2.0, "ctx": 1_000_000},
    "qwen3.7-max": {"publisher": "Alibaba", "input": 0.5, "output": 2.0, "ctx": 1_000_000},
    "qwen3.7-plus": {"publisher": "Alibaba", "input": 0.25, "output": 1.0, "ctx": 1_000_000},
    # DeepSeek
    "deepseek-v4-pro": {"publisher": "DeepSeek", "input": 0.3, "output": 1.2, "ctx": 128_000},
    "deepseek-v4-flash": {"publisher": "DeepSeek", "input": 0.05, "output": 0.2, "ctx": 128_000},
    # MiniMax
    "minimax-m3": {"publisher": "MiniMax", "input": 0.5, "output": 2.0, "ctx": 256_000},
    # Xiaomi MiMo
    "mimo-v2.5-pro": {"publisher": "Xiaomi", "input": 0.3, "output": 1.2, "ctx": 128_000},
    "mimo-v2.5": {"publisher": "Xiaomi", "input": 0.15, "output": 0.6, "ctx": 128_000},
    # Muse
    "muse-spark-1.3": {"publisher": "Muse", "input": 0.2, "output": 0.8, "ctx": 128_000},
}


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Backfill key_aliases for seed models with empty aliases.
    #    Seed models have name = probe id (e.g. "claude-opus-4-8") but
    #    key_aliases = []. Set key_aliases = [name] so alias-based lookups work.
    rows = conn.execute(
        sa.text("SELECT id, name, key_aliases FROM ai_models WHERE owner_scope = 'platform'")
    ).fetchall()
    for row in rows:
        model_id, name, raw_aliases = row
        aliases = raw_aliases if isinstance(raw_aliases, list) else []
        if not aliases and name:
            conn.execute(
                sa.text("UPDATE ai_models SET key_aliases = :aliases WHERE id = :id"),
                {"aliases": json.dumps([name]), "id": model_id},
            )

    # 2. Merge ca-* duplicate models into canonical originals.
    #    A ca-* model (e.g. "ca-3.8-max") is a duplicate alias for an existing
    #    model whose name equals the suffix after "ca-" (e.g. "qwen3.8-max").
    #    Move the ca-* id into the original's key_aliases, re-point any
    #    ai_key_model_bindings from the ca-* row to the original, delete ca-*.
    ca_rows = conn.execute(
        sa.text("SELECT id, name FROM ai_models WHERE name LIKE 'ca-%' AND owner_scope = 'platform'")
    ).fetchall()
    for ca_id, ca_name in ca_rows:
        suffix = ca_name[3:]  # strip "ca-"
        # Find the original by name = suffix (case-insensitive).
        orig = conn.execute(
            sa.text("SELECT id, key_aliases FROM ai_models WHERE lower(name) = :n AND owner_scope = 'platform' AND id != :caid"),
            {"n": suffix.lower(), "caid": ca_id},
        ).fetchone()
        if orig is None:
            # No canonical original found — leave the ca-* row as-is.
            continue
        orig_id, orig_aliases = orig
        aliases = orig_aliases if isinstance(orig_aliases, list) else []
        if ca_name not in [str(a) for a in aliases]:
            aliases = list(aliases) + [ca_name]
            conn.execute(
                sa.text("UPDATE ai_models SET key_aliases = :aliases WHERE id = :id"),
                {"aliases": json.dumps(aliases), "id": orig_id},
            )
        # Re-point bindings: if the key already has a binding on the original,
        # drop the ca-* binding; else move it.
        dup_bindings = conn.execute(
            sa.text(
                "SELECT id, key_id, enabled, is_default FROM ai_key_model_bindings WHERE model_id = :caid"
            ),
            {"caid": ca_id},
        ).fetchall()
        for bid, key_id, enabled, is_default in dup_bindings:
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
        # Delete SDK bindings + the ca-* model row.
        conn.execute(sa.text("DELETE FROM ai_model_sdk_bindings WHERE model_id = :caid"), {"caid": ca_id})
        conn.execute(sa.text("DELETE FROM ai_models WHERE id = :caid"), {"caid": ca_id})

    # 3. Fill publisher / pricing / context for known models.
    for name, meta in _MODEL_META.items():
        conn.execute(
            sa.text(
                "UPDATE ai_models "
                "SET publisher = COALESCE(publisher, :pub), "
                "    input_price_usd_per_mtok = COALESCE(input_price_usd_per_mtok, :inp), "
                "    output_price_usd_per_mtok = COALESCE(output_price_usd_per_mtok, :outp), "
                "    max_context_tokens = COALESCE(max_context_tokens, :ctx) "
                "WHERE lower(name) = :n AND owner_scope = 'platform'"
            ),
            {
                "pub": meta["publisher"],
                "inp": meta["input"],
                "outp": meta["output"],
                "ctx": meta["ctx"],
                "n": name.lower(),
            },
        )


def downgrade() -> None:
    # Data migration — no schema to revert. Aliases/ca-merge/pricing are
    # intentionally one-way; downgrade is a no-op.
    pass
