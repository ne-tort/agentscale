"""Реквизиты компании отдельной таблицей document_company_fields (chats=all —
весь кабинет): поставщик/город/приложение/сроки с дефолтами из шаблона +
seq-счётчики номеров договора/спецификации; document_fields остаётся сделочным
(chats=current). Панель «Бюджетирования» теперь двухгрупповая (doc_fields:
company_table/deal_table/company_fields/deal_fields+auto), экспорты мерджат
company+deal (params company_fields_table).

Meta documents (tables/columns/views) are SoT-seeded — refresh platform +
instance copies via upsert_product_modules.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100506"
down_revision = "2026100505"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
