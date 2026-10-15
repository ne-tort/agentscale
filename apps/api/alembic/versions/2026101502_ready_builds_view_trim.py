"""Готовые сборки: убраны столбцы «Под заказ»/«Совместима»/«Без компонента» + подсказка целевой цены.

Изменения только в определениях вида (ui_json) — обновляются через
upsert_product_modules. Данные не затрагиваются: колонки on_order/compat_ok/
unresolved_count остаются в таблице и продолжают считаться пайплайном, просто
не выводятся в списке.
"""

from __future__ import annotations

import logging

from alembic import op

from prodavan.application.platform.product_module_upsert import upsert_product_modules

logger = logging.getLogger(__name__)

revision = "2026101502"
down_revision = "2026101501"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)
    logger.info("ready_builds list view refreshed (columns trimmed, target price hint)")


def downgrade() -> None:
    pass
