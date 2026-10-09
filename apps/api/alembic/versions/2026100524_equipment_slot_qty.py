"""WAVE11 фаза 1: количество на слот сборки (`equipment_builds.slot_qty`).

`slot_qty` = {type_id: qty} — параллельная карта к `slots` = {type_id: group_id}.
Количество — свойство СЛОТА, а не кандидата: у слота может быть несколько
альтернатив, но «2 плашки ОЗУ» верно для любой из них. Цена слота =
цена выбранного кандидата × qty (и в пайплайне, и в клиентском пересчёте).

Отдельная карта (а не вложение qty внутрь `slots`) выбрана сознательно: не
ломает существующие сборки WAVE10 и pick-логику выбора кандидата.

Seeds only — refresh platform + instance copies via upsert_product_modules.
"""

import sqlalchemy as sa

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100524"
down_revision = "2026100523"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE module_instance_data_rows
            SET body = body - 'slot_qty'
            WHERE table_slug = 'equipment_builds' AND body ? 'slot_qty'
            """
        )
    )
