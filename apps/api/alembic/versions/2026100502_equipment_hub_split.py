"""Equipment hub split: вторая материнская страница «Подбор техники» в
«Управление» (базы данных, типы комплектующих, поставщики, интернет-магазины,
шаблоны) — view equipment_hub_management + tab placement=management; в
«Данных» остаются рабочие таблицы (позиции/товары/закупка/бюджет/сборка).

Meta documents (tables/columns/views/tabs) are SoT-seeded — refresh platform +
instance copies via upsert_product_modules.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100502"
down_revision = "2026100501"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
