"""Готовые сборки: вид выбора компонента из пула группы (UX-ревизия).

Меняется только мета модуля (виды) — данные не мигрируются:
- новый вид `build_group_items_pick` — таблица пула группы для замены компонента
  в слоте: фильтруется по открытому слоту (группа сборки + класс/тип слота) и
  пишет выбранную строку пула в `item_id` слота через set_on_context;
- `ready_build_slot_settings.item_id` стал таким пикером (`widget: "pick"`)
  вместо нефильтрованного ref-выпадающего списка всех компонентов пула.

Сиды идемпотентны (upsert_product_modules), данные не трогаются.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100532"
down_revision = "2026100531"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
