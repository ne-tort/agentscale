"""«Найденные товары»: без офферов — «Нет оффера» warning в «Товаре»
(без подстановки партномера), PN-фолбэк из позиции заказчика, прочерки;
Бюджетирование/Закупка первыми в хабе «Данных».

Seeds only — refresh platform + instance copies via upsert_product_modules.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100518"
down_revision = "2026100517"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
