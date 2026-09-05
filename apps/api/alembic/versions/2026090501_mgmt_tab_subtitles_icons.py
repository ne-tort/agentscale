"""Refresh product module tabs: unique icons + management hub subtitles."""

import json

import sqlalchemy as sa
from alembic import op

from prodavan.application.platform.product_module_seeds import (
    mod_files_meta,
    mod_mcp_meta,
    mod_prompts_meta,
)

revision = "2026090501"
down_revision = "2026090302"
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
        meta = meta_fn()
        conn.execute(
            sa.text(
                """
                UPDATE module_meta_documents
                SET body = CAST(:body AS jsonb)
                WHERE module_id = :module_id AND slug = 'tabs'
                """
            ),
            {"module_id": module_id, "body": json.dumps(meta["tabs"])},
        )


def downgrade() -> None:
    pass
