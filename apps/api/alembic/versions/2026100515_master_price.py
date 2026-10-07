"""Мастер-прайс в «Подбор техники»: флаг master_price у поставщиков,
встроенный шаблон «Прайс» (layout легаси-Commerce), виртуальная таблица
master_price (строки из OpenSearch, серверная пагинация/поиск) + страница
в «Данных» и экшен скачивания.

Seeds only — refresh platform + instance copies via upsert_product_modules.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100515"
down_revision = "2026100514"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
