"""Employee UI gaps — project about, AI key scope, project modules."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026082901"
down_revision = "2026082820"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("about", sa.Text(), nullable=True))
    op.add_column(
        "projects",
        sa.Column(
            "resolved_ai_key_id",
            sa.String(length=40),
            sa.ForeignKey("ai_provider_keys.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_table(
        "project_module_bindings",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("project_id", sa.String(length=40), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("module_id", sa.String(length=40), sa.ForeignKey("modules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "module_id", name="uq_project_module_binding"),
    )
    op.create_table(
        "employee_ai_key_bindings",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("employee_id", sa.String(length=40), sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key_id", sa.String(length=40), sa.ForeignKey("ai_provider_keys.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("employee_id", "key_id", name="uq_employee_ai_key"),
    )
    op.create_table(
        "cabinet_ai_key_bindings",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("cabinet_id", sa.String(length=40), sa.ForeignKey("cabinet_instances.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key_id", sa.String(length=40), sa.ForeignKey("ai_provider_keys.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("cabinet_id", "key_id", name="uq_cabinet_ai_key"),
    )


def downgrade() -> None:
    op.drop_table("cabinet_ai_key_bindings")
    op.drop_table("employee_ai_key_bindings")
    op.drop_table("project_module_bindings")
    op.drop_column("projects", "resolved_ai_key_id")
    op.drop_column("projects", "about")
