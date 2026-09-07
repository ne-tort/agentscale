"""Re-upsert product modules — trusted_sellers + web_shops tables/views."""

from alembic import op

from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026090705"
down_revision = "2026090704"
branch_labels = None
depends_on = None


def upgrade() -> None:
    upsert_product_modules(op.get_bind())


def downgrade() -> None:
    pass
