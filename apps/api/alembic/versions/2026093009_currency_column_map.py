"""refresh catalog column-map schema (currency key)"""

from alembic import op  # noqa: I001

from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026093009"
down_revision = "2026093008"


def upgrade() -> None:
    """Refresh product module meta (currency in the catalog column-map schema)."""
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
