"""Fix global MC binds for management modules; upsert equipment MCP."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026091102"
down_revision = "2026091101"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    # Historical 2026090711 forced MC global+locked for management modules.
    # Product model: company→cabinet is always a local copy; cabinet→project may be global.
    conn.execute(
        sa.text(
            """
            UPDATE module_cabinet_bindings
            SET bind_kind = 'local', child_may_edit = true
            WHERE module_id IN ('mod_prompts', 'mod_mcp', 'mod_files')
              AND bind_kind = 'global'
            """
        )
    )
    # Platform→company grants: prefer local copies (admin hands company a fork).
    conn.execute(
        sa.text(
            """
            UPDATE module_company_grants
            SET bind_kind = 'local', child_may_edit = true
            WHERE module_id IN ('mod_prompts', 'mod_mcp', 'mod_files')
              AND bind_kind = 'global'
              AND status = 'active'
            """
        )
    )
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
