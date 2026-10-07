"""Текстовые «цены» («Уточняйте») и точность: warning-окраска.

- offer_price format: цена оффера/лица группы без числа → «Нет цены» warning;
- бюджет: снапшот match_kind + warning_when_match на наименовании/P/N для
  аналогов и сомнений;
- best-оффер: наличие → цена → приоритет (безценовой выигрывает только
  внутри своей группы наличие, когда priced-варианта там нет).

Seeds only — refresh platform + instance copies via upsert_product_modules.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100514"
down_revision = "2026100513"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
