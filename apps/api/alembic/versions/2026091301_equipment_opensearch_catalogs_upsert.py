"""Upsert mod_equipment meta: OpenSearch catalogs (drop sqlite merge / EQUIPMENT_*)."""

from alembic import op

from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026091301"
down_revision = "2026091201"
branch_labels = None
depends_on = None


def upgrade() -> None:
    upsert_product_modules(op.get_bind())


def downgrade() -> None:
    pass
