"""Re-upsert product modules — catalog settings visible_when / no status UI."""

from alembic import op

from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026090605"
down_revision = "2026090604"
branch_labels = None
depends_on = None


def upgrade() -> None:
    upsert_product_modules(op.get_bind())


def downgrade() -> None:
    pass
