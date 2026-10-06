"""Сроки переезжают из группы «Поставщик» в «Сделку»: delivery_days,
payment_days, lead_time_note теперь колонки document_fields (chats=current,
per-chat) и поля deal-группы панели реквизитов; дефолты из шаблона сохранены.

Meta documents (tables/columns/views) are SoT-seeded — refresh platform +
instance copies via upsert_product_modules.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100508"
down_revision = "2026100507"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
