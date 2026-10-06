"""UI-правка реквизитов и заголовков: панель документов = две карточки
(«Поставщик» + «Сделка») в одну линию со сводкой, без общего заголовка;
дата договора по умолчанию = сегодняшняя (auto: today); заголовок
дрилл-дауна «Товары {seller}» из строки закупки (scaffold.title_template).

Meta documents (tables/columns/views) are SoT-seeded — refresh platform +
instance copies via upsert_product_modules.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100507"
down_revision = "2026100506"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
