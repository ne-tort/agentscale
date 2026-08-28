"""Drop legacy project_runtime_units after project_pods migration."""

from alembic import op
import sqlalchemy as sa

revision = "2026082819"
down_revision = "2026082818"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("fk_projects_primary_runtime_unit", "projects", type_="foreignkey")
    op.drop_column("projects", "primary_runtime_unit_id")
    op.drop_table("project_runtime_units")


def downgrade() -> None:
    op.create_table(
        "project_runtime_units",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="primary"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("runtime_ref", sa.String(length=128), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.add_column("projects", sa.Column("primary_runtime_unit_id", sa.String(length=40), nullable=True))
    op.create_foreign_key(
        "fk_projects_primary_runtime_unit",
        "projects",
        "project_runtime_units",
        ["primary_runtime_unit_id"],
        ["id"],
        ondelete="SET NULL",
        use_alter=True,
    )
