"""«Интернет товары» (web_products) — таблица + MCP-инструменты.

Сидирует через upsert_product_modules:
- таблица web_products (title, part_number, brand, price, shop, url, qty, line_id);
- 4 MCP-инструмента: web_shops_list, web_products_list, web_product_add,
  web_product_delete.

TTL 72 часа обслуживается celery-задачей sweep_web_products_ttl (beat 15 мин),
а не SQL-триггером: строки живут в общей module_instance_data_rows, и политика
по table_slug не должна задевать другие модули.
"""

from alembic import op

from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026102101"
down_revision = "2026101901"


def upgrade() -> None:
    upsert_product_modules(op.get_bind())


def downgrade() -> None:
    pass
