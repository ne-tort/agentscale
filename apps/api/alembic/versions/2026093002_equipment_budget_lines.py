"""Equipment budget_lines: table, views (chat_header), actions, MCP tool.

Upserts mod_equipment meta with the budget page: budget_lines table
(chat-scoped snapshot of request_lines best offers), budget_lines_list view
(computed price_out/margin_total columns, row_tap → offers_for_line via
context_field), hub tile, budget_sync_lines / budget_export actions and the
budget_lines_list read-only MCP tool.
"""

from alembic import op

from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026093002"
down_revision = "2026093001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    upsert_product_modules(op.get_bind())


def downgrade() -> None:
    pass
