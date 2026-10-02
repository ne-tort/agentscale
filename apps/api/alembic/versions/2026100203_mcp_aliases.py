"""MCP chat display aliases: mcp_aliases meta document for product modules.

- mod_equipment gets seeded aliases for all 8 prodavan-equipment package
  tools (Russian labels shown in chat instead of
  "MCP: prodavan-equipment.<tool>");
- validator learns the mcp_aliases array slug (tool/server/label/description);
- upsert_product_modules refreshes module + instance meta docs.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100203"
down_revision = "2026100202"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
