"""Upsert mod_equipment: found_offers list shows line_id as Запрос."""

from alembic import op

from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026091408"
down_revision = "2026091407"
branch_labels = None
depends_on = None


def upgrade() -> None:
    upsert_product_modules(op.get_bind())


def downgrade() -> None:
    pass
