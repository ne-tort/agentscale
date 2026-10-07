"""Бюджет: честный «Вход с НДС» (price_in=null + on_order, warning-надписи),
автовыбор best не берёт офферы без цены при наличии priced-альтернатив;
список офферов группы: наличие сверху, подзаказные — warning.

Seeds only — refresh platform + instance copies via upsert_product_modules.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100513"
down_revision = "2026100512"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
