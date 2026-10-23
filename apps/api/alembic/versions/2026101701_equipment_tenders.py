"""WAVE14 «Тендеры»: глобальная таблица tenders + колонки/представления/действия.

Тендер — глобальная (chats=all) группировка позиций заказчика: состав в
lines_json, агрегаты (позиций/найдено %/сумма/маржа/поставщики) пишет
_sync_tenders оборотного пайплайна; чат берёт тендер в работу действием
tender_attach (позиции копируются в request_lines со связкой tender_id +
tender_line_id). Физических таблиц не создаём — всё в module meta/data rows.
"""

from alembic import op

from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026101701"
down_revision = "2026101502"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
