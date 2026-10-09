"""WAVE10 финализация: переприменение сидов модуля «Подбор техники».

Revision `2026100520` (та же ветка WAVE10) уже проставилась на dev до того, как
доехали финальные правки сидов: вид `found_groups_pick`, колонка
`found_groups.owner_kind`, снапшот `request_lines.build_*`, обновлённый
`equipment_types.fields_json`, MCP 2.3.0 и правило `60-builds.md`. Сиды —
идемпотентный SoT (`upsert_product_modules`), поэтому достаточно новой ревизии,
чтобы обновить platform + instance meta (пользовательские строки не трогаются:
`ON CONFLICT DO NOTHING`).

Seeds only — данные не мигрируются.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100521"
down_revision = "2026100520"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
