"""Страница «Поиск товаров» (виртуальная OS-таблица) + сопоставление
найденного товара с позицией заказчика (звёздочка=выбранный оффер).

Seeds only — refresh platform + instance copies via upsert_product_modules.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100517"
down_revision = "2026100516"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
