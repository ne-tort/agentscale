"""Готовые сборки: колонки классовых характеристик и подписи (UX-ревизия).

Меняется только мета модуля (колонки/виды) — данные не мигрируются:
- `ready_builds.gpu_gb` (GPU, ГБ) и производные `ram_sticks` / `storage_drives`
  (численность ОЗУ и накопителей из слотов своего типа);
- подписи «Слотов» → «Типов комплектующих» и «Всего штук» → «Компонентов всего»
  (раньше путались);
- в вид `build_groups_list` добавлена «Сборки до ₽» (price_max раньше не показывался).

Сиды идемпотентны (`upsert_product_modules` → `ON CONFLICT DO NOTHING` для
строк данных, `DO UPDATE` для meta), пользовательские строки не трогаются.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100530"
down_revision = "2026100529"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
