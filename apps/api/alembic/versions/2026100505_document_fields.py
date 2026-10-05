"""Document fields (реквизиты КП/Спецификации): таблица document_fields +
панель ввода в «Бюджетировании» + params fields_table у экспортов; КП/Специф.
теперь генерируются кодом (PDF ReportLab / XLSX openpyxl) вместо Gotenberg.

Meta documents (tables/columns/views) are SoT-seeded — refresh platform +
instance copies via upsert_product_modules.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100505"
down_revision = "2026100504"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
