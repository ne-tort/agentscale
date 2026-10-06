"""Equipment wave7 fixups: procurement shared scope (chats=all, проектный
агрегат вместо per-chat зеркал), face_stale/budget brand колонки, on_load
сверка бюджета, кросс-чатовый supplier_offers, форма оффера без связующих
полей, MCP prodavan-equipment 2.1.0 (delete-инструменты, статусы позиций —
только пайплайн).

Meta documents (tables/columns/views) are SoT-seeded — refresh platform +
instance copies via upsert_product_modules.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100401"
down_revision = "2026100301"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
