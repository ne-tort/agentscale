"""WAVE10: правка сборки триггерит пайплайн (builds_table в params экшена).

`maybe_auto_budget_sync` следил только за found_groups / request_lines /
found_offers, поэтому записи в `equipment_builds` (ручной выбор сборки
`is_selected`, смена позиции или состава) НЕ пересчитывали best-сборку, бюджет
и статус позиции — они догоняли только при открытии страницы (on_load).

Добавлен `builds_table` в params `equipment_pipeline_sync` + таблица в набор
наблюдаемых. Материализация для сборок не запускается (только пересчёт).

Seeds only — refresh platform + instance copies via upsert_product_modules.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100523"
down_revision = "2026100522"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
