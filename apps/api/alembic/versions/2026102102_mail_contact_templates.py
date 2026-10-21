"""«Интернет товары» + контакты-шаблоны manager/s4b: сид в существующие инсталляции.

Предыдущие ревизии сидировали web_products и MCP-инструменты, но контакты
mail_contacts (manager, s4b) добавлены позже — их нужно донести до кабинетов,
уже развёрнутых на dev.
"""

from alembic import op

from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026102102"
down_revision = "2026102101"


def upgrade() -> None:
    upsert_product_modules(op.get_bind())


def downgrade() -> None:
    pass
