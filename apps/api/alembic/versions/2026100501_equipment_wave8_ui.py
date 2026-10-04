"""Equipment wave8 UI/связность: found_groups +face_priority/alternatives_count,
вьюхи групп без зелёных строк (бейдж «Выгода» вместо них), сортировка
точность→приоритет→цена, «Альтернативы», бюджетная «Маржа» {n} ({y}%)
selection-only, позиции без столбца «Статус». MCP prodavan-equipment 2.1.1
(чёткие инструкции по P/N-алиасам/src_hash и автовыбору).

Meta documents (tables/columns/views) are SoT-seeded — refresh platform +
instance copies via upsert_product_modules.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100501"
down_revision = "2026100401"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
