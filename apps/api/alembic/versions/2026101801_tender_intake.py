"""WAVE15: тендеры — статус irrelevant, поля источника, внешний intake.

- tenders.status += irrelevant (отбраковка агентом/вручную), row_style
  warning на списке;
- tenders: source_url / source_ref / relevance_note (внешний intake);
- external API: POST /external/projects/{id}/tenders (+attach+kickoff);
- промпт AGENTS.md модуля: работа с тендерами, отбраковка нерелевантных;
- MCP prodavan-equipment 3.2.0: tender_upsert принимает source_*/relevance_note,
  статусы +irrelevant.
"""

from alembic import op

from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026101801"
down_revision = "2026101702"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
