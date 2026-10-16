"""Готовые сборки: MCP-инструменты «Слот готовой сборки» (UX-ревизия).

Меняется только мета модуля — данные не мигрируются:
- новый инструмент `ready_build_slot_upsert` (продavan-equipment, MCP 2.4.4) и
  его алиас в `mcp_aliases` — патч ОДНОГО слота сборки, а не перезапись всего
  массива, как делал `ready_build_upsert`.

Сиды идемпотентны (upsert_product_modules), данные не трогаются.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100533"
down_revision = "2026100532"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
