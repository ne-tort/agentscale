"""Cabinet N:M grants + owner_scope."""

import sqlalchemy as sa

from alembic import op

revision = "2026082702"
down_revision = "2026082701"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cabinet_instances",
        sa.Column("owner_scope", sa.String(length=32), server_default="platform", nullable=False),
    )
    op.add_column(
        "cabinet_instances",
        sa.Column("owner_company_id", sa.String(length=40), nullable=True),
    )
    op.create_foreign_key(
        "cabinet_instances_owner_company_id_fkey",
        "cabinet_instances",
        "companies",
        ["owner_company_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "cabinet_company_grants",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("cabinet_id", sa.String(length=40), sa.ForeignKey("cabinet_instances.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", sa.String(length=40), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mode", sa.String(length=32), server_default="assigned_ro", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("cabinet_id", "company_id", name="uq_cabinet_company_grant"),
    )
    op.create_index("ix_cabinet_company_grants_company_id", "cabinet_company_grants", ["company_id"])

    op.create_table(
        "cabinet_employee_assignments",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("cabinet_id", sa.String(length=40), sa.ForeignKey("cabinet_instances.id", ondelete="CASCADE"), nullable=False),
        sa.Column("employee_id", sa.String(length=40), sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(length=32), server_default="operator", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("cabinet_id", "employee_id", name="uq_cabinet_employee_assignment"),
    )
    op.create_index("ix_cabinet_employee_assignments_employee_id", "cabinet_employee_assignments", ["employee_id"])

    # Backfill company grants from legacy company_id.
    op.execute(
        """
        INSERT INTO cabinet_company_grants (id, cabinet_id, company_id, mode, status)
        SELECT 'ccg_' || substr(md5(cabinet_instances.id || ':' || cabinet_instances.company_id), 1, 16),
               cabinet_instances.id,
               cabinet_instances.company_id,
               'assigned_ro',
               'active'
        FROM cabinet_instances
        WHERE cabinet_instances.company_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """
    )

    # Backfill employee assignments from legacy owner_employee_id.
    op.execute(
        """
        INSERT INTO cabinet_employee_assignments (id, cabinet_id, employee_id, role, status)
        SELECT 'cas_' || substr(md5(cabinet_instances.id || ':' || cabinet_instances.owner_employee_id), 1, 16),
               cabinet_instances.id,
               cabinet_instances.owner_employee_id,
               'operator',
               'active'
        FROM cabinet_instances
        WHERE cabinet_instances.owner_employee_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """
    )

    # owner_scope: employee-created → company; admin-created (no owner) → platform.
    op.execute(
        """
        UPDATE cabinet_instances
        SET owner_scope = 'company',
            owner_company_id = company_id
        WHERE owner_employee_id IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE cabinet_instances
        SET owner_scope = 'platform',
            owner_company_id = NULL
        WHERE owner_employee_id IS NULL
        """
    )

    op.drop_constraint("cabinet_instances_company_id_fkey", "cabinet_instances", type_="foreignkey")
    op.alter_column("cabinet_instances", "company_id", existing_type=sa.String(length=40), nullable=True)
    op.create_foreign_key(
        "cabinet_instances_company_id_fkey",
        "cabinet_instances",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.execute("UPDATE cabinet_instances SET company_id = owner_company_id WHERE company_id IS NULL AND owner_company_id IS NOT NULL")
    op.drop_constraint("cabinet_instances_company_id_fkey", "cabinet_instances", type_="foreignkey")
    op.alter_column("cabinet_instances", "company_id", existing_type=sa.String(length=40), nullable=False)
    op.create_foreign_key(
        "cabinet_instances_company_id_fkey",
        "cabinet_instances",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_index("ix_cabinet_employee_assignments_employee_id", table_name="cabinet_employee_assignments")
    op.drop_table("cabinet_employee_assignments")
    op.drop_index("ix_cabinet_company_grants_company_id", table_name="cabinet_company_grants")
    op.drop_table("cabinet_company_grants")
    op.drop_constraint("cabinet_instances_owner_company_id_fkey", "cabinet_instances", type_="foreignkey")
    op.drop_column("cabinet_instances", "owner_company_id")
    op.drop_column("cabinet_instances", "owner_scope")
