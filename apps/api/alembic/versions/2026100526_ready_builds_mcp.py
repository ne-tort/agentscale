"""WAVE11 фаза 4: MCP-инструменты каталога сборок + действие копирования в чат.

Ревизия `2026100525` уже применена, а сиды после неё изменились — нужна новая
ревизия, иначе `upsert_product_modules` не перезапустится и meta не догонит
(известные грабли: см. `2026100521`).

Что доезжает:
- действие `ready_build_attach` (`kind: equipment.attach_ready_build`) —
  копирование готовой сборки из каталога в чат на позицию заказчика
  (копируются КЛЮЧИ, а не цены; дальше работает штатный пайплайн WAVE10);
- колонка `equipment_builds.source_ready_build_id` — провенанс копии;
- MCP `prodavan-equipment` 2.4.0: `ready_builds_catalog`, `build_group_upsert`,
  `build_group_item_upsert`, `ready_build_upsert`, `ready_build_attach`
  (+ `mcp_aliases` на них и версия в строке `equipment_mcp_default`);
- промпты: правило `70-ready-builds.md` и обновления `AGENTS.md` /
  `60-builds.md` (приоритет «сначала готовая сборка, потом с нуля»).

Seeds only — refresh platform + instance copies via upsert_product_modules.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100526"
down_revision = "2026100525"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
