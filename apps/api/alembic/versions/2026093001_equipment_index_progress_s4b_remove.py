"""Equipment module: indexing progress fields, s4b removal, RO seller tools.

- catalogs gains hidden progress columns (indexed_count / total_rows /
  indexing_started_at): the Celery worker heartbeats them every bulk chunk so
  the UI can render «В процессе (x из y)» and the beat sweep can heal rows
  stuck in `indexing` after a worker restart (deploy).
- s4b_settings (table, views, hub tile, materialize rule, S4B_* container env)
  is removed from the module meta; existing s4b_settings data rows are deleted.
- generic MCP tools: legacy disabled equipment_catalog_query, duplicate
  equipment_offers_upsert and the RW trusted_sellers_upsert / web_shops_upsert
  are removed — sellers / web shops stay **read-only** for the agent (list).
"""

from alembic import op
import sqlalchemy as sa

from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026093001"
down_revision = "2026092104"
branch_labels = None
depends_on = None


def _has_table(conn: sa.Connection, name: str) -> bool:
    row = conn.execute(
        sa.text(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = :n
            """
        ),
        {"n": name},
    ).fetchone()
    return row is not None


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)

    # Drop leftover s4b_settings data rows (settings/integration data is not
    # migrated anywhere: the S4B feature is removed).
    if _has_table(conn, "module_instance_data_rows"):
        result = conn.execute(
            sa.text(
                "DELETE FROM module_instance_data_rows WHERE table_slug = 's4b_settings'"
            )
        )
        deleted = getattr(result, "rowcount", 0) or 0
        if deleted:
            print(f"s4b_settings rows deleted: {deleted}")


def downgrade() -> None:
    # Meta is restored from seeds on the next deploy; data rows are gone.
    pass
