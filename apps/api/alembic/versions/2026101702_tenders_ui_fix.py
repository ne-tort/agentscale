"""WAVE14.1: пересид мета «Тендеры» — inline_add вместо primary_action.

Список тендеров переехал на канонический паттерн модуля: `inline_add`
(поле «Добавить тендер» над таблицей) вместо `primary_action: create_row`
(кнопка с сырым kind в тулбаре). Форма очищена от read-only агрегатов.
"""

from alembic import op

from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026101702"
down_revision = "2026101701"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
