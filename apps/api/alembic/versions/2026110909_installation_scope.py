"""Add trusted installation-parent scope for private plugin bootstrap.

Revision ID: 2026110909
Revises: 2026110908

Additive only. Existing ModuleSource rows receive nullable scope columns and no
installation binding is backfilled. Legacy/random/ambiguous sources remain
fail-closed until the trusted source/admin flow proves a ProjectRow binding.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2026110909"
down_revision: str | Sequence[str] | None = "2026110908"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_project_tenant_cabinet_anchor",
        "projects",
        ["id", "company_id", "cabinet_id"],
    )
    op.create_unique_constraint(
        "uq_plugin_instance_tenant_anchor",
        "plugin_instances",
        ["company_id", "id"],
    )

    op.add_column(
        "plugin_module_sources",
        sa.Column("cabinet_id", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "plugin_module_sources",
        sa.Column("project_id", sa.String(length=40), nullable=True),
    )
    op.create_check_constraint(
        "ck_plugin_module_source_scope_pair",
        "plugin_module_sources",
        "(cabinet_id IS NULL AND project_id IS NULL) OR "
        "(cabinet_id IS NOT NULL AND project_id IS NOT NULL)",
    )
    op.create_foreign_key(
        "fk_plugin_module_source_project_scope",
        "plugin_module_sources",
        "projects",
        ["project_id", "company_id", "cabinet_id"],
        ["id", "company_id", "cabinet_id"],
        ondelete="RESTRICT",
    )

    op.add_column(
        "plugin_source_reservations",
        sa.Column("cabinet_id", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "plugin_source_reservations",
        sa.Column("project_id", sa.String(length=40), nullable=True),
    )
    op.create_check_constraint(
        "ck_plugin_source_reservation_scope_pair",
        "plugin_source_reservations",
        "(cabinet_id IS NULL AND project_id IS NULL) OR "
        "(cabinet_id IS NOT NULL AND project_id IS NOT NULL)",
    )
    op.create_foreign_key(
        "fk_plugin_source_reservation_project_scope",
        "plugin_source_reservations",
        "projects",
        ["project_id", "company_id", "cabinet_id"],
        ["id", "company_id", "cabinet_id"],
        ondelete="RESTRICT",
    )

    # No INSERT/UPDATE/backfill: only the trusted source/admin flow creates rows.
    op.create_table(
        "plugin_installation_scopes",
        sa.Column("plugin_instance_id", sa.String(length=80), primary_key=True),
        sa.Column("company_id", sa.String(length=40), nullable=False),
        sa.Column("cabinet_id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "company_id", "cabinet_id"],
            ["projects.id", "projects.company_id", "projects.cabinet_id"],
            name="fk_plugin_installation_scope_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "plugin_instance_id"],
            ["plugin_instances.company_id", "plugin_instances.id"],
            name="fk_plugin_installation_scope_instance",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_plugin_installation_scope_company",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "company_id <> '' AND company_id = btrim(company_id)",
            name="ck_plugin_installation_scope_company_nonempty",
        ),
        sa.CheckConstraint(
            "cabinet_id <> '' AND cabinet_id = btrim(cabinet_id)",
            name="ck_plugin_installation_scope_cabinet_nonempty",
        ),
        sa.CheckConstraint(
            "project_id <> '' AND project_id = btrim(project_id)",
            name="ck_plugin_installation_scope_project_nonempty",
        ),
    )


def downgrade() -> None:
    op.drop_table("plugin_installation_scopes")
    op.drop_constraint(
        "fk_plugin_source_reservation_project_scope",
        "plugin_source_reservations",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_plugin_source_reservation_scope_pair",
        "plugin_source_reservations",
        type_="check",
    )
    op.drop_column("plugin_source_reservations", "project_id")
    op.drop_column("plugin_source_reservations", "cabinet_id")
    op.drop_constraint(
        "fk_plugin_module_source_project_scope",
        "plugin_module_sources",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_plugin_module_source_scope_pair",
        "plugin_module_sources",
        type_="check",
    )
    op.drop_column("plugin_module_sources", "project_id")
    op.drop_column("plugin_module_sources", "cabinet_id")
    op.drop_constraint(
        "uq_plugin_instance_tenant_anchor", "plugin_instances", type_="unique"
    )
    op.drop_constraint(
        "uq_project_tenant_cabinet_anchor", "projects", type_="unique"
    )
