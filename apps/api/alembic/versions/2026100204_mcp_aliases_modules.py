"""Neutral chat tool labels + prodavan-modules MCP aliases.

- l10n: group/tool MCP labels lose the "MCP:" tech prefix, Russian plurals
  («N вызова инструмента»), «Список инструментов», typo «Подагент»→«Субагент»;
- mod_mcp seed: mcp_aliases for the 9 prodavan-modules platform tools
  (modules_list, module_meta_*, module_data_*, module_action_invoke) —
  previously rendered as "prodavan-modules · modules_list".
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100204"
down_revision = "2026100203"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
