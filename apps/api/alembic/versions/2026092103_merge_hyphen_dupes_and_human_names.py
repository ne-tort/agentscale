"""Merge hyphen/point duplicate models + rename to human-readable names.

After 2026092101 (alias backfill + ca-* merge) and 2026092102 (explicit ca-*
mapping) the catalog still has duplicates where the same model is registered
twice under a hyphen id (e.g. "claude-fable-5-1", provider=cursor) and a point
id (e.g. "claude-fable-5.1", provider=codex, already carrying a ca-* alias).
The point-named entry is the canonical one (it has multiple aliases); the
hyphen-named single-alias duplicate is merged into it and deleted.

Also collapses exact duplicate rows (gpt-5.1 / gpt-5.2 / gpt-5.3-codex each
exist twice with identical aliases) into a single entry, moving SDK bindings.

Finally renames every catalog entry to a human-readable display name
("Claude Opus 4.6" instead of "claude-opus-4.6") — the model_ids/key_aliases
stay untouched so probe matching is unaffected.

Data migration only — no schema changes. Downgrade is a no-op (one-way rename
+ merge; the original machine-style names are recoverable from key_aliases).
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import op

revision = "2026092103"
down_revision = "2026092102"
branch_labels = None
depends_on = None


# Duplicate pairs: two model ids that refer to the same underlying model but
# differ only in separator (hyphen vs point), e.g. "claude-fable-5-1" and
# "claude-fable-5.1". The migration merges whichever one has fewer aliases
# into the one with more aliases (the multi-alias entry is canonical — it
# typically carries a ca-* alias from 2026092102). If both have a single
# alias the point variant wins (vendor catalog names use points).
_DUP_PAIRS: list[tuple[str, str]] = [
    ("claude-fable-5-1", "claude-fable-5.1"),
    ("claude-haiku-4-5", "claude-haiku-4.5"),
    ("claude-opus-4-6", "claude-opus-4.6"),
    ("claude-opus-4-7", "claude-opus-4.7"),
    ("claude-opus-4-8", "claude-opus-4.8"),
    ("claude-sonnet-4-6", "claude-sonnet-4.6"),
]


# Human-readable display name per canonical model id (the lower-cased name as
# it is after the merge). Entries not listed keep their current name.
_HUMAN_NAMES: dict[str, str] = {
    # Anthropic Claude
    "claude-opus-5": "Claude Opus 5",
    "claude-opus-4.8": "Claude Opus 4.8",
    "claude-opus-4.7": "Claude Opus 4.7",
    "claude-opus-4.6": "Claude Opus 4.6",
    "claude-opus-4-8": "Claude Opus 4.8",
    "claude-opus-4-7": "Claude Opus 4.7",
    "claude-opus-4-6": "Claude Opus 4.6",
    "claude-opus-4-5": "Claude Opus 4.5",
    "claude-sonnet-5": "Claude Sonnet 5",
    "claude-sonnet-4.6": "Claude Sonnet 4.6",
    "claude-sonnet-4-6": "Claude Sonnet 4.6",
    "claude-sonnet-4-5": "Claude Sonnet 4.5",
    "claude-sonnet-4": "Claude Sonnet 4",
    "claude-haiku-4.5": "Claude Haiku 4.5",
    "claude-haiku-4-5": "Claude Haiku 4.5",
    "claude-fable-5.1": "Claude Fable 5.1",
    "claude-fable-5-1": "Claude Fable 5.1",
    "claude-fable-5": "Claude Fable 5",
    # OpenAI GPT
    "gpt-6-astra": "GPT 6 Astra",
    "gpt-5.6-sol": "GPT 5.6 Sol",
    "gpt-5.6-terra": "GPT 5.6 Terra",
    "gpt-5.6-luna": "GPT 5.6 Luna",
    "gpt-5.5": "GPT 5.5",
    "gpt-5.4": "GPT 5.4",
    "gpt-5.4-mini": "GPT 5.4 Mini",
    "gpt-5.4-nano": "GPT 5.4 Nano",
    "gpt-5.3-codex": "GPT 5.3 Codex",
    "gpt-5.2": "GPT 5.2",
    "gpt-5.1": "GPT 5.1",
    "gpt-5-mini": "GPT 5 Mini",
    # Google Gemini
    "gemini-3.8-flash": "Gemini 3.8 Flash",
    "gemini-3.7-flash": "Gemini 3.7 Flash",
    "gemini-3.6-flash": "Gemini 3.6 Flash",
    "gemini-3.5-flash": "Gemini 3.5 Flash",
    "gemini-3-flash": "Gemini 3 Flash",
    "gemini-3.1-pro": "Gemini 3.1 Pro",
    "gemini-2.5-flash": "Gemini 2.5 Flash",
    # xAI Grok
    "grok-4.6": "Grok 4.6",
    "grok-4.5": "Grok 4.5",
    # OpenAI Composer
    "composer-2.5": "Composer 2.5",
    "composer-2.5-fast": "Composer 2.5 Fast",
    "composer-2": "Composer 2",
    # Moonshot Kimi
    "kimi-k3": "Kimi K3",
    "kimi-k2.7-code": "Kimi K2.7 Code",
    # Zhipu GLM
    "glm-5.3": "GLM 5.3",
    "glm-5.3-flash": "GLM 5.3 Flash",
    "glm-5-turbo": "GLM 5 Turbo",
    "glm-5.2": "GLM 5.2",
    # Alibaba Qwen
    "qwen3.8-max": "Qwen 3.8 Max",
    "qwen3.7-max": "Qwen 3.7 Max",
    "qwen3.7-plus": "Qwen 3.7 Plus",
    # DeepSeek
    "deepseek-v4-pro": "DeepSeek V4 Pro",
    "deepseek-v4-flash": "DeepSeek V4 Flash",
    # MiniMax
    "minimax-m3": "MiniMax M3",
    # Xiaomi MiMo
    "mimo-v2.5-pro": "MiMo V2.5 Pro",
    "mimo-v2.5": "MiMo V2.5",
    # Muse
    "muse-spark-1.3": "Muse Spark 1.3",
    # Default placeholder
    "default": "Default",
}


# Metadata backfill for entries still missing publisher / pricing / context
# after 2026092101. Anthropic pricing is the live public API pricing as of
# Sep 2026 (https://www.anthropic.com/pricing); other vendors are best-effort
# public pricing. Fills only NULLs (COALESCE) — never overwrites.
_META_BACKFILL: dict[str, dict] = {
    # Anthropic — live API pricing Sep 2026.
    "claude-opus-5": {"publisher": "Anthropic", "input": 5.0, "output": 25.0, "ctx": 200_000},
    "claude-opus-4.8": {"publisher": "Anthropic", "input": 5.0, "output": 25.0, "ctx": 200_000},
    "claude-opus-4.7": {"publisher": "Anthropic", "input": 5.0, "output": 25.0, "ctx": 200_000},
    "claude-opus-4.6": {"publisher": "Anthropic", "input": 5.0, "output": 25.0, "ctx": 200_000},
    "claude-opus-4-8": {"publisher": "Anthropic", "input": 5.0, "output": 25.0, "ctx": 200_000},
    "claude-opus-4-7": {"publisher": "Anthropic", "input": 5.0, "output": 25.0, "ctx": 200_000},
    "claude-opus-4-6": {"publisher": "Anthropic", "input": 5.0, "output": 25.0, "ctx": 200_000},
    "claude-opus-4-5": {"publisher": "Anthropic", "input": 5.0, "output": 25.0, "ctx": 200_000},
    "claude-sonnet-5": {"publisher": "Anthropic", "input": 2.0, "output": 10.0, "ctx": 200_000},
    "claude-sonnet-4.6": {"publisher": "Anthropic", "input": 3.0, "output": 15.0, "ctx": 200_000},
    "claude-sonnet-4-6": {"publisher": "Anthropic", "input": 3.0, "output": 15.0, "ctx": 200_000},
    "claude-sonnet-4-5": {"publisher": "Anthropic", "input": 3.0, "output": 15.0, "ctx": 200_000},
    "claude-sonnet-4": {"publisher": "Anthropic", "input": 3.0, "output": 15.0, "ctx": 200_000},
    "claude-haiku-4.5": {"publisher": "Anthropic", "input": 1.0, "output": 5.0, "ctx": 200_000},
    "claude-haiku-4-5": {"publisher": "Anthropic", "input": 1.0, "output": 5.0, "ctx": 200_000},
    "claude-fable-5.1": {"publisher": "Anthropic", "input": 10.0, "output": 50.0, "ctx": 200_000},
    "claude-fable-5-1": {"publisher": "Anthropic", "input": 10.0, "output": 50.0, "ctx": 200_000},
    "claude-fable-5": {"publisher": "Anthropic", "input": 10.0, "output": 50.0, "ctx": 200_000},
}


def _row_by_name(conn, name: str) -> tuple[str, list] | None:
    r = conn.execute(
        sa.text(
            "SELECT id, key_aliases FROM ai_models "
            "WHERE lower(name) = :n AND owner_scope = 'platform'"
        ),
        {"n": name.lower()},
    ).fetchone()
    if r is None:
        return None
    raw = r[1]
    aliases = raw if isinstance(raw, list) else []
    return r[0], aliases


def _merge_duplicates(conn) -> None:
    # 1. Hyphen/point separator duplicate pairs (same model, two ids).
    for a, b in _DUP_PAIRS:
        ra = _row_by_name(conn, a)
        rb = _row_by_name(conn, b)
        if ra is None or rb is None:
            continue
        # Canonical = the entry with more aliases; tie -> point variant (b).
        if len(ra[1]) > len(rb[1]):
            canon, dup = ra, rb
        else:
            canon, dup = rb, ra
        canon_id, canon_aliases = canon
        dup_id, _ = dup
        # Add both ids (hyphen + point) to the canonical aliases so probe
        # matching works regardless of separator style.
        merged = list(canon_aliases)
        for alias in (a, b):
            if alias not in [str(x) for x in merged]:
                merged.append(alias)
        if merged != list(canon_aliases):
            conn.execute(
                sa.text("UPDATE ai_models SET key_aliases = :a WHERE id = :id"),
                {"a": json.dumps(merged), "id": canon_id},
            )
        _delete_row_repoint_bindings(conn, dup_id, canon_id)

    # 2. Exact duplicate rows (same name, identical aliases) — collapse to a
    # single entry. Canonical = the row with the most aliases; ties broken by
    # id order (seed ids sort deterministically). SDK bindings missing on the
    # canonical row are moved before the duplicate is deleted.
    names = conn.execute(
        sa.text(
            "SELECT lower(name) FROM ai_models WHERE owner_scope = 'platform' "
            "GROUP BY lower(name) HAVING COUNT(*) > 1"
        )
    ).fetchall()
    for (lname,) in names:
        rows = conn.execute(
            sa.text(
                "SELECT id, key_aliases FROM ai_models "
                "WHERE lower(name) = :n AND owner_scope = 'platform' ORDER BY id"
            ),
            {"n": lname},
        ).fetchall()
        # Pick canonical: most aliases, then first by id.
        best_idx = 0
        best_aliases = -1
        for i, r in enumerate(rows):
            raw = r[1]
            al = raw if isinstance(raw, list) else []
            n = len(al)
            if n > best_aliases:
                best_aliases = n
                best_idx = i
        keep_id = rows[best_idx][0]
        for i, r in enumerate(rows):
            if i == best_idx:
                continue
            _move_sdk_bindings(conn, src_id=r[0], dst_id=keep_id)
            _delete_row_repoint_bindings(conn, dup_id=r[0], canon_id=keep_id)


def _move_sdk_bindings(conn, src_id: str, dst_id: str) -> None:
    src_kinds = {
        k[0]
        for k in conn.execute(
            sa.text("SELECT api_kind FROM ai_model_sdk_bindings WHERE model_id = :id"),
            {"id": src_id},
        ).fetchall()
    }
    dst_kinds = {
        k[0]
        for k in conn.execute(
            sa.text("SELECT api_kind FROM ai_model_sdk_bindings WHERE model_id = :id"),
            {"id": dst_id},
        ).fetchall()
    }
    for kind in src_kinds:
        if kind not in dst_kinds:
            conn.execute(
                sa.text(
                    "UPDATE ai_model_sdk_bindings SET model_id = :dst "
                    "WHERE model_id = :src AND api_kind = :k"
                ),
                {"dst": dst_id, "src": src_id, "k": kind},
            )
            dst_kinds.add(kind)


def _delete_row_repoint_bindings(conn, dup_id: str, canon_id: str) -> None:
    # Re-point key↔model bindings: if the key already binds the canonical
    # entry, drop the duplicate binding; else move it.
    dup_bindings = conn.execute(
        sa.text(
            "SELECT id, key_id FROM ai_key_model_bindings WHERE model_id = :dup"
        ),
        {"dup": dup_id},
    ).fetchall()
    for bid, key_id in dup_bindings:
        existing = conn.execute(
            sa.text(
                "SELECT id FROM ai_key_model_bindings WHERE key_id = :kid AND model_id = :canon"
            ),
            {"kid": key_id, "canon": canon_id},
        ).fetchone()
        if existing is None:
            conn.execute(
                sa.text("UPDATE ai_key_model_bindings SET model_id = :canon WHERE id = :bid"),
                {"canon": canon_id, "bid": bid},
            )
        else:
            conn.execute(sa.text("DELETE FROM ai_key_model_bindings WHERE id = :bid"), {"bid": bid})
    conn.execute(sa.text("DELETE FROM ai_model_sdk_bindings WHERE model_id = :dup"), {"dup": dup_id})
    conn.execute(sa.text("DELETE FROM ai_models WHERE id = :dup"), {"dup": dup_id})


def _rename_human(conn) -> None:
    for name, human in _HUMAN_NAMES.items():
        conn.execute(
            sa.text(
                "UPDATE ai_models SET name = :human "
                "WHERE lower(name) = :n AND owner_scope = 'platform'"
            ),
            {"human": human, "n": name.lower()},
        )


def _backfill_metadata(conn) -> None:
    for name, meta in _META_BACKFILL.items():
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


def upgrade() -> None:
    conn = op.get_bind()
    _merge_duplicates(conn)
    _backfill_metadata(conn)
    _rename_human(conn)


def downgrade() -> None:
    # Data migration — one-way merge + rename. Original machine-style names are
    # recoverable from key_aliases; no schema to revert.
    pass
