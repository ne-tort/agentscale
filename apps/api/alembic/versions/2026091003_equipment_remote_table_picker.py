"""Upsert product modules — remote table picker + DSN strip."""

from alembic import op

from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026091003"
down_revision = "2026091002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    upsert_product_modules(op.get_bind())


def downgrade() -> None:
    pass
