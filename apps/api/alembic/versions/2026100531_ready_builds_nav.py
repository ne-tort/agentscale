"""Готовые сборки: навигация между страницами каталога (UX-ревизия).

Меняется только мета модуля (виды) — данные не мигрируются:
- `build_groups_list` открывает настройки группы, а не сразу список сборок —
  иначе группу (socket/ram_type/budget_tier) нельзя было редактировать вовсе
  (build_group_settings был недостижим);
- `build_group_settings` получил поля-ссылки «Пул компонентов» и «Сборки группы»;
- `build_group_items_list` фильтруется по открытой группе (row_filter_from_context)
  и ведёт в настройки компонента;
- `ready_build_settings` получил ссылку «Слоты сборки»;
- `ready_build_slots_list` фильтруется по открытой сборке и ведёт в настройки слота.

До этого четыре вида были недостижимы из UI (орфаны): их открытия ниоткуда не
ссылались. Сиды идемпотентны (upsert_product_modules), данные не трогаются.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100531"
down_revision = "2026100530"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
