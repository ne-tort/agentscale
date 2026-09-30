"""sellers registry + budget scaffold sync

Revision ID: 2026093005
Revises: 2026093004
Create Date: 2026-09-30

Product module meta refresh (idempotent upsert):
- mod_equipment: «Поставщики» (trusted_sellers rework — flags/margin/requisites/
  unique name+aliases, disabled-warning row style), budget_sync moved to the
  budget view scaffold actions (app bar), found_offers.seller column;
- behavior changes live in code (budget_sync seller mapping + margin init,
  catalog search supplier filter/priority, unique column validation).
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026093005"
down_revision = "2026093004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
