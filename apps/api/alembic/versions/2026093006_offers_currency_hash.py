"""suppliers auto-fill + offer currency/hash columns refresh"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026093006"
down_revision = "2026093005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
