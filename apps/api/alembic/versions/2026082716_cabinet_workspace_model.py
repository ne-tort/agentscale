"""Cabinet workspace model — template copies, max_projects, project creator metadata."""

from alembic import op
import sqlalchemy as sa

revision = "2026082716"
down_revision = "2026082715"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cabinet_instances",
        sa.Column("template_cabinet_id", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "cabinet_instances",
        sa.Column("max_projects", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_cabinet_instances_template_cabinet_id",
        "cabinet_instances",
        "cabinet_instances",
        ["template_cabinet_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.alter_column("projects", "owner_employee_id", existing_type=sa.String(length=40), nullable=True)
    op.drop_constraint("projects_owner_employee_id_fkey", "projects", type_="foreignkey")
    op.create_foreign_key(
        "projects_owner_employee_id_fkey",
        "projects",
        "employees",
        ["owner_employee_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("projects_owner_employee_id_fkey", "projects", type_="foreignkey")
    op.create_foreign_key(
        "projects_owner_employee_id_fkey",
        "projects",
        "employees",
        ["owner_employee_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column("projects", "owner_employee_id", existing_type=sa.String(length=40), nullable=False)

    op.drop_constraint(
        "fk_cabinet_instances_template_cabinet_id",
        "cabinet_instances",
        type_="foreignkey",
    )
    op.drop_column("cabinet_instances", "max_projects")
    op.drop_column("cabinet_instances", "template_cabinet_id")
