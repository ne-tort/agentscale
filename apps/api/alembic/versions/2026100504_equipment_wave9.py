"""Equipment wave9: закупка (qty_total, доставка всегда расход, include_delivery
убран), промпты подбора (equipment_prompts, materialize prompt_paths), оверрайды
инструкций MCP (mcp_tool_overrides), аннотации офферов (alternatives_count/
benefit_label/match_label), face_in_stock/match_label на группах. MCP 2.2.0.

Meta documents (tables/columns/views) are SoT-seeded — refresh platform +
instance copies via upsert_product_modules.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100504"
down_revision = "2026100503"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
