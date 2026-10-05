"""Budget view fixups: «Маржа» всегда видна (selection_only снят — пустая
колонка выглядела багом; режим выделения теперь гейтирует колонку действий),
партномер уже (100→80px).

Meta documents (tables/columns/views) are SoT-seeded — refresh platform +
instance copies via upsert_product_modules.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100503"
down_revision = "2026100502"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
