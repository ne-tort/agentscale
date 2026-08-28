"""Project service model — visibility, runtime units, assignments."""

from alembic import op
import sqlalchemy as sa

revision = "2026082717"
down_revision = "2026082716"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("visibility_mode", sa.String(length=32), nullable=False, server_default="cabinet_shared"),
    )

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

    op.add_column(
        "projects",
        sa.Column("primary_runtime_unit_id", sa.String(length=40), nullable=True),
    )
    op.create_foreign_key(
        "fk_projects_primary_runtime_unit",
        "projects",
        "project_runtime_units",
        ["primary_runtime_unit_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "project_employee_assignments",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("cabinet_id", sa.String(length=40), nullable=False),
        sa.Column("employee_id", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["cabinet_id"], ["cabinet_instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "employee_id", name="uq_project_employee_assignments"),
    )

    # Backfill primary runtime units from legacy container_ref.
    import uuid

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, container_ref FROM projects WHERE container_ref IS NOT NULL AND container_ref != ''"
        )
    ).fetchall()
    for project_id, container_ref in rows:
        unit_id = f"pru_{uuid.uuid4().hex[:16]}"
        conn.execute(
            sa.text(
                """
                INSERT INTO project_runtime_units (id, project_id, kind, status, runtime_ref)
                VALUES (:id, :project_id, 'primary', 'running', :runtime_ref)
                """
            ),
            {"id": unit_id, "project_id": project_id, "runtime_ref": container_ref},
        )
        conn.execute(
            sa.text("UPDATE projects SET primary_runtime_unit_id = :unit_id WHERE id = :project_id"),
            {"unit_id": unit_id, "project_id": project_id},
        )


def downgrade() -> None:
    op.drop_table("project_employee_assignments")
    op.drop_constraint("fk_projects_primary_runtime_unit", "projects", type_="foreignkey")
    op.drop_column("projects", "primary_runtime_unit_id")
    op.drop_table("project_runtime_units")
    op.drop_column("projects", "visibility_mode")
