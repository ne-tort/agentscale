"""WAVE10 — сборки ПК/серверов: связь «Позиция → Сборка → Найденный товар».

Новые колонки (seeds → meta документы; данные не мигрируются, все поля
опциональны и заполняются по мере работы):
- `found_groups.build_id` / `slot_type_id` — группа-кандидат слота сборки;
- `equipment_builds.line_id` + `is_best`/`is_selected`/`alternatives_count`/
  `match_kind`/`match_label`/`benefit_label`/`benefit_tone`/`note`;
- `budget_lines.build_id` — строка бюджета может быть сборкой.

Seeds only — refresh platform + instance copies via upsert_product_modules.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100520"
down_revision = "2026100519"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
