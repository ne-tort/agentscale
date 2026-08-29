"""Project draft lifecycle: materialize_manifest + migrate active-without-pod to draft."""

import json

import sqlalchemy as sa
from alembic import op

from prodavan.application.platform.product_module_seeds import (
    mod_files_meta,
    mod_mcp_meta,
    mod_prompts_meta,
)

revision = "2026082906"
down_revision = "2026082905"
branch_labels = None
depends_on = None

_MODULE_ROOTS = {
    "mod_prompts": ["rules/", "skills/", "prompts/", "AGENTS.md", "CLAUDE.md"],
    "mod_mcp": ["packages/"],
    "mod_files": [],
}

_WHEN_SYNC = "project.sync"


def _append_sync_when(rules: list) -> list:
    out: list = []
    for rule in rules:
        if not isinstance(rule, dict):
            out.append(rule)
            continue
        when = list(rule.get("when") or [])
        if _WHEN_SYNC not in when:
            when.append(_WHEN_SYNC)
        patched = dict(rule)
        patched["when"] = when
        out.append(patched)
    return out


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("materialize_manifest", sa.dialects.postgresql.JSONB(), nullable=True),
    )
    conn = op.get_bind()
    # Active projects without a live pod → draft (configured-but-not-launched legacy rows).
    conn.execute(
        sa.text(
            """
            UPDATE projects p
            SET status = 'draft'
            WHERE p.status = 'active'
              AND NOT EXISTS (
                SELECT 1 FROM project_pods pp
                WHERE pp.project_id = p.id
                  AND pp.status NOT IN ('terminated', 'failed')
              )
            """
        )
    )
    for module_id, roots in _MODULE_ROOTS.items():
        doc_id = f"mmd_{module_id.removeprefix('mod_')}_materialize_roots"
        conn.execute(
            sa.text(
                """
                INSERT INTO module_meta_documents (id, module_id, slug, body)
                VALUES (:id, :module_id, 'materialize_roots', CAST(:body AS jsonb))
                ON CONFLICT (module_id, slug) DO UPDATE SET body = EXCLUDED.body
                """
            ),
            {
                "id": doc_id,
                "module_id": module_id,
                "body": json.dumps({"workspace_roots": roots}),
            },
        )
    meta_map = {
        "mod_prompts": mod_prompts_meta,
        "mod_mcp": mod_mcp_meta,
        "mod_files": mod_files_meta,
    }
    for module_id, meta_fn in meta_map.items():
        meta = meta_fn()
        rules = _append_sync_when(meta.get("materialize") or [])
        conn.execute(
            sa.text(
                """
                UPDATE module_meta_documents
                SET body = CAST(:body AS jsonb)
                WHERE module_id = :module_id AND slug = 'materialize'
                """
            ),
            {"module_id": module_id, "body": json.dumps(rules)},
        )


def downgrade() -> None:
    op.drop_column("projects", "materialize_manifest")
