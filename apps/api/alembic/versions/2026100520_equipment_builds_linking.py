"""WAVE10 — сборки ПК/серверов: связь «Позиция → Сборка → Найденный товар».

Новые колонки (seeds → meta документы; данные не мигрируются, все поля
опциональны и заполняются по мере работы):
- `found_groups.build_id` / `slot_type_id` — группа-кандидат слота сборки;
- `equipment_builds.line_id` + `is_best`/`is_selected`/`alternatives_count`/
  `match_kind`/`match_label`/`benefit_label`/`benefit_tone`/`note`;
- `budget_lines.build_id` — строка бюджета может быть сборкой;
- `request_lines.builds_count` / `build_best_id` / `build_best_price` /
  `build_match_label` / `build_benefit_label` / `build_benefit_tone` —
  снапшот сборок позиции (пишет пайплайн, read-only в UI).

Также обновлены виды (seeds): `found_groups_pick` (кандидаты слота с выбором
best), `equipment_builds_list`/`equipment_build_settings` (сборка↔позиция),
аудит `equipment_types.fields_json` (ключи совместимости), MCP
prodavan-equipment 2.3.0 (инструменты сборок/типов).

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
