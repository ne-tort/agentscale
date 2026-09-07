"""Re-upsert product modules — web_shops cookies via text_editor nav page."""

from alembic import op

from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026090706"
down_revision = "2026090705"
branch_labels = None
depends_on = None


def upgrade() -> None:
    upsert_product_modules(op.get_bind())


def downgrade() -> None:
    pass
