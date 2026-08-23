"""Projects runtime tables.

Revision ID: projects_001
Revises: admin_company_001
Create Date: 2026-08-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "projects_001"
down_revision: str | None = "admin_company_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("company_id", sa.String(length=40), nullable=False),
        sa.Column("cabinet_id", sa.String(length=40), nullable=False),
        sa.Column("owner_employee_id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("workspace_key", sa.String(length=64), nullable=False),
        sa.Column("container_ref", sa.String(length=128), nullable=False),
        sa.Column("agent_provider", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cabinet_id"], ["cabinet_instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("workspace_key", name="uq_project_workspace_key"),
        sa.UniqueConstraint("cabinet_id", "slug", name="uq_project_cabinet_slug"),
    )
    op.create_index("ix_projects_cabinet", "projects", ["cabinet_id"])
    op.create_index("ix_projects_owner", "projects", ["owner_employee_id"])

    op.create_table(
        "project_triggers",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_project_triggers_project", "project_triggers", ["project_id"])

    op.create_table(
        "project_attachments",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("filename", sa.String(length=260), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_ref", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_project_attachments_project", "project_attachments", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_project_attachments_project", table_name="project_attachments")
    op.drop_table("project_attachments")
    op.drop_index("ix_project_triggers_project", table_name="project_triggers")
    op.drop_table("project_triggers")
    op.drop_index("ix_projects_owner", table_name="projects")
    op.drop_index("ix_projects_cabinet", table_name="projects")
    op.drop_table("projects")
