"""Add session_id to module_instance_data_rows; upsert equipment chats scope."""

from alembic import op
import sqlalchemy as sa

from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026091401"
down_revision = "2026091302"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "module_instance_data_rows",
        sa.Column("session_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_module_instance_data_rows_session",
        "module_instance_data_rows",
        ["instance_id", "table_slug", "session_id"],
        unique=False,
    )
    upsert_product_modules(op.get_bind())


def downgrade() -> None:
    op.drop_index(
        "ix_module_instance_data_rows_session",
        table_name="module_instance_data_rows",
    )
    op.drop_column("module_instance_data_rows", "session_id")
