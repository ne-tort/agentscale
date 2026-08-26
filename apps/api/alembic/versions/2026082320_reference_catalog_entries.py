"""Reference catalog entries table + AI HTTP providers are seeded on first list."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "2026082320"
down_revision = "2026082319"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reference_catalog_entries",
        sa.Column("pk", sa.String(length=40), primary_key=True),
        sa.Column("catalog_id", sa.String(length=64), nullable=False),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("subtitle", sa.String(length=500), nullable=True),
        sa.Column("icon_name", sa.String(length=64), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("seeded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("catalog_id", "id", name="uq_reference_catalog_entry"),
    )
    op.create_index("ix_reference_catalog_entries_catalog_id", "reference_catalog_entries", ["catalog_id"])


def downgrade() -> None:
    op.drop_index("ix_reference_catalog_entries_catalog_id", table_name="reference_catalog_entries")
    op.drop_table("reference_catalog_entries")
