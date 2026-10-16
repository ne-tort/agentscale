"""Готовые сборки: идентичность слота (несколько компонентов одного типа).

Добавлена колонка `found_groups.slot_id` (скрытая): идентичность КОНКРЕТНОГО
слота сборки. Без неё два компонента одного типа (например SSD + HDD в одной
сборке) схлопывались в один слот — пайплайн группировал кандидатов по
(build_id, slot_type_id). Теперь по (build_id, slot_id), с fallback на
slot_type_id для старых данных.

`slot_id` по умолчанию = `slot_type_id`, поэтому сборки с одним компонентом на
тип (подавляющее большинство) ведут себя ровно как раньше.

Меняется только мета модуля (колонка) — данные не мигрируются. Сиды
идемпотентны (upsert_product_modules), данные не трогаются.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100534"
down_revision = "2026100533"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
