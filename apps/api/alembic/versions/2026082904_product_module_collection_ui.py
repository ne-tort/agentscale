"""Patch product module collection views: inline_add.title, drop primary_action."""

import json

import sqlalchemy as sa
from alembic import op

from prodavan.application.platform.product_module_seeds import (
    mod_files_meta,
    mod_mcp_meta,
    mod_prompts_meta,
)

revision = "2026082904"
down_revision = "2026082903"
branch_labels = None
depends_on = None

_MODULE_META = (
    ("mod_prompts", mod_prompts_meta),
    ("mod_mcp", mod_mcp_meta),
    ("mod_files", mod_files_meta),
)


def upgrade() -> None:
    conn = op.get_bind()
    for module_id, meta_fn in _MODULE_META:
        views = meta_fn()["views"]
        conn.execute(
            sa.text(
                """
                UPDATE module_meta_documents
                SET body = CAST(:body AS jsonb)
                WHERE module_id = :module_id AND slug = 'views'
                """
            ),
            {"module_id": module_id, "body": json.dumps(views)},
        )


def downgrade() -> None:
    conn = op.get_bind()
    legacy_primary = {"kind": "create_row", "label": "Добавить"}

    for module_id, meta_fn in _MODULE_META:
        views = meta_fn()["views"]
        patched = []
        for view in views:
            if view.get("kind") != "collection":
                patched.append(view)
                continue
            v = dict(view)
            ui = dict(v.get("ui_json") or {})
            ui["primary_action"] = legacy_primary
            field = ui.get("inline_add", {}).get("field", "name")
            ui["inline_add"] = {"field": field, "label": "Новый item"}
            v["ui_json"] = ui
            patched.append(v)
        conn.execute(
            sa.text(
                """
                UPDATE module_meta_documents
                SET body = CAST(:body AS jsonb)
                WHERE module_id = :module_id AND slug = 'views'
                """
            ),
            {"module_id": module_id, "body": json.dumps(patched)},
        )
