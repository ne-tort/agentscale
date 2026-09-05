"""Create module_instances + instance meta/data tables."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "2026090503"
down_revision = "2026090502"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "module_instances",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("module_id", sa.String(length=40), sa.ForeignKey("modules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_kind", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column(
            "parent_instance_id",
            sa.String(length=40),
            sa.ForeignKey("module_instances.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("owner_kind", "owner_id", "module_id", name="uq_module_instance_owner"),
    )
    op.create_index("ix_module_instances_module_id", "module_instances", ["module_id"])

    op.create_table(
        "module_instance_meta_documents",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column(
            "instance_id",
            sa.String(length=40),
            sa.ForeignKey("module_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("instance_id", "slug", name="uq_module_instance_meta_slug"),
    )

    op.create_table(
        "module_instance_data_rows",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column(
            "instance_id",
            sa.String(length=40),
            sa.ForeignKey("module_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("table_slug", sa.String(length=64), nullable=False),
        sa.Column("row_id", sa.String(length=64), nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_by", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("instance_id", "table_slug", "row_id", name="uq_module_instance_data_row"),
    )
    op.create_index(
        "ix_module_instance_data_rows_instance_table",
        "module_instance_data_rows",
        ["instance_id", "table_slug"],
    )


def downgrade() -> None:
    op.drop_index("ix_module_instance_data_rows_instance_table", table_name="module_instance_data_rows")
    op.drop_table("module_instance_data_rows")
    op.drop_table("module_instance_meta_documents")
    op.drop_index("ix_module_instances_module_id", table_name="module_instances")
    op.drop_table("module_instances")
