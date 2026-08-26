"""cabinet_instances.owner_employee_id nullable for Admin-created cabinets."""

import sqlalchemy as sa

from alembic import op

revision = "2026082701"
down_revision = "2026082621"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "cabinet_instances_owner_employee_id_fkey",
        "cabinet_instances",
        type_="foreignkey",
    )
    op.alter_column(
        "cabinet_instances",
        "owner_employee_id",
        existing_type=sa.String(length=40),
        nullable=True,
    )
    op.create_foreign_key(
        "cabinet_instances_owner_employee_id_fkey",
        "cabinet_instances",
        "employees",
        ["owner_employee_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE cabinet_instances SET owner_employee_id = "
        "(SELECT id FROM employees LIMIT 1) WHERE owner_employee_id IS NULL"
    )
    op.drop_constraint(
        "cabinet_instances_owner_employee_id_fkey",
        "cabinet_instances",
        type_="foreignkey",
    )
    op.alter_column(
        "cabinet_instances",
        "owner_employee_id",
        existing_type=sa.String(length=40),
        nullable=False,
    )
    op.create_foreign_key(
        "cabinet_instances_owner_employee_id_fkey",
        "cabinet_instances",
        "employees",
        ["owner_employee_id"],
        ["id"],
        ondelete="CASCADE",
    )
