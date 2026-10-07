"""Мастер-прайс: РРЦ из каталога + наценка поставщика.

- индексация каталогов: каноническая колонка rrc (РРЦ источника) → OS-доки
  rrc/rrc_num (правила Commerce: РРЦ хранится сырой, наценка только при
  формировании мастер-прайса);
- мастер-прайс: цена и РРЦ = сырые × (1 + margin_pct/100) поставщика;
- «Поставщики»: колонка «Мастер прайс» в списке; страница мастер-прайса:
  колонка РРЦ.

Seeds only — refresh platform + instance copies via upsert_product_modules.
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100516"
down_revision = "2026100515"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
