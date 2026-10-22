"""Ручное добавление товара в «Мастер прайс»: таблица + форма в существующие
инсталляции.

Новые сущности мета-манифеста mod_equipment:
- таблица `master_price_manual` (колонки: продавец, наименование, PN, бренд,
  цена, валюта, РРЦ, наличие, срок);
- вьюха `master_price_manual_form` (kind=form) — её открывает кнопка «+»
  рядом с пагинатором мастер-прайса (`master_price_list.ui_json.add_view`).

Без этой ревизии у уже развёрнутых кабинетов не было бы ни таблицы, ни формы,
и кнопка «+» открывала бы несуществующую вьюху. Сид идемпотентен
(upsert_product_modules раскладывает его по всем инстанциям модуля).
"""

from alembic import op

from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026102202"
down_revision = "2026102201"


def upgrade() -> None:
    upsert_product_modules(op.get_bind())


def downgrade() -> None:
    pass
