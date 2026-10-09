"""WAVE10 фиксы логики: флаг `equipment_builds.on_order` + backfill
`found_groups.owner_kind`.

1. `on_order` — явный флаг «хотя бы один компонент сборки не в наличии».
   Бюджет раньше определял это подстрокой «под заказ» в `match_label`
   (хрупко: формулировка/локализация метки может меняться).

2. Backfill `owner_kind` на существующих `found_groups`. Колонка появилась в
   `2026100520`, но заполняется только прогоном пайплайна; списки
   «Найденные товары» фильтруют по `owner_kind = line`, поэтому без backfill
   уже созданные группы пропадают из вида до первого sync'а.

Seeds only + точечный backfill; пользовательские данные не переписываются.
"""

import sqlalchemy as sa

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100522"
down_revision = "2026100521"

_BACKFILL_OWNER_KIND = sa.text(
    """
    UPDATE module_instance_data_rows
    SET body = jsonb_set(
        body,
        '{owner_kind}',
        to_jsonb(CASE WHEN coalesce(body->>'build_id', '') <> ''
                      THEN 'build' ELSE 'line' END)
    )
    WHERE table_slug = 'found_groups'
      AND coalesce(body->>'owner_kind', '') = ''
    """
)


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)
    conn.execute(_BACKFILL_OWNER_KIND)


def downgrade() -> None:
    pass
