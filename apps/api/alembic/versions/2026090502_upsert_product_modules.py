"""Refresh all product module meta from PRODUCT_MODULES (idempotent upsert)."""

from alembic import op

from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026090502"
down_revision = "2026090501"
branch_labels = None
depends_on = None


def upgrade() -> None:
    upsert_product_modules(op.get_bind())


def downgrade() -> None:
    pass
