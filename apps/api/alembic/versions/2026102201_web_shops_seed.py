"""«Интернет магазины»: сид магазинов (DNS, Citilink, Regard, NIX, М.Видео,
Ozon, Wildberries, Avito) в существующие инсталляции.

Таблица `web_shops` была описана в meta-манифесте, но строк в ней не было ни
у одного кабинета — `web_shops_list` возвращал пустой список, и агент не мог
искать товары в интернете (проверено на живом стенде 2026-10-22: агент
отчитался «Список интернет-магазинов проекта пуст — DNS туда не заведён»).

Сидим через upsert_product_modules: он идемпотентно раскладывает seed_rows по
ВСЕМ инстанциям модуля, поэтому магазины появляются и у уже развёрнутых
кабинетов, и у новых.
"""

from alembic import op

from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026102201"
down_revision = "2026102102"


def upgrade() -> None:
    upsert_product_modules(op.get_bind())


def downgrade() -> None:
    pass
