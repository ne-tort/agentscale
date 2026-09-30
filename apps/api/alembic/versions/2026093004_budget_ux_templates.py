"""refresh product modules: budgeting UX + mod_templates

Revision ID: 2026093004
Revises: 2026093003
Create Date: 2026-09-30

- mod_equipment: per-column table config (max_lines/max_width/align), summary
  strip, editable vat/markup cells, scaffold export actions (budget xlsx,
  КП PDF, Спецификация PDF) + new actions kp_export/spec_export;
- new product module mod_templates («Шаблоны») — uploadable document
  templates (budget / commercial_proposal / specification).
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026093004"
down_revision = "2026093003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    # Meta refresh only; module templates are data, no schema change to revert.
    pass
