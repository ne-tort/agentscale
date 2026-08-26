"""Module registry + bindings."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "2026082703"
down_revision = "2026082702"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "modules",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_table(
        "module_meta_documents",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("module_id", sa.String(length=40), sa.ForeignKey("modules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("module_id", "slug", name="uq_module_meta_document_slug"),
    )
    op.create_index("ix_module_meta_documents_module_id", "module_meta_documents", ["module_id"])
    op.create_table(
        "module_cabinet_bindings",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("module_id", sa.String(length=40), sa.ForeignKey("modules.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "cabinet_id",
            sa.String(length=40),
            sa.ForeignKey("cabinet_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("module_id", "cabinet_id", name="uq_module_cabinet_binding"),
    )
    op.create_index("ix_module_cabinet_bindings_cabinet_id", "module_cabinet_bindings", ["cabinet_id"])
    op.create_table(
        "module_project_bindings",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("module_id", sa.String(length=40), sa.ForeignKey("modules.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "project_id",
            sa.String(length=40),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("module_id", "project_id", name="uq_module_project_binding"),
    )
    op.create_index("ix_module_project_bindings_project_id", "module_project_bindings", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_module_project_bindings_project_id", table_name="module_project_bindings")
    op.drop_table("module_project_bindings")
    op.drop_index("ix_module_cabinet_bindings_cabinet_id", table_name="module_cabinet_bindings")
    op.drop_table("module_cabinet_bindings")
    op.drop_index("ix_module_meta_documents_module_id", table_name="module_meta_documents")
    op.drop_table("module_meta_documents")
    op.drop_table("modules")
