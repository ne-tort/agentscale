"""Project pods — 1:1 runtime unit per project."""

from alembic import op
import sqlalchemy as sa

revision = "2026082818"
down_revision = "2026082717"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_pods",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=True),
        sa.Column("workspace_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("desired_state", sa.String(length=32), nullable=False, server_default="absent"),
        sa.Column("runtime_ref", sa.String(length=128), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("hydrate_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_project_pods_project_id",
        "project_pods",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "uq_project_pods_live_project",
        "project_pods",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("project_id IS NOT NULL AND status NOT IN ('terminated')"),
    )

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            """
            SELECT pru.id, pru.project_id, pru.status, pru.runtime_ref, p.workspace_key, p.container_ref
            FROM project_runtime_units pru
            JOIN projects p ON p.id = pru.project_id
            WHERE pru.kind = 'primary'
              AND pru.status != 'deleted'
            """
        )
    ).fetchall()
    import uuid

    for _unit_id, project_id, status, runtime_ref, workspace_key, container_ref in rows:
        pod_id = f"pod_{uuid.uuid4().hex[:16]}"
        mapped_status = "running" if status == "running" else "paused" if status == "paused" else "pending"
        desired = "running" if status == "running" else "absent"
        ref = runtime_ref or container_ref
        conn.execute(
            sa.text(
                """
                INSERT INTO project_pods (
                    id, project_id, workspace_key, status, desired_state, runtime_ref
                ) VALUES (
                    :id, :project_id, :workspace_key, :status, :desired_state, :runtime_ref
                )
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "id": pod_id,
                "project_id": project_id,
                "workspace_key": workspace_key,
                "status": mapped_status,
                "desired_state": desired,
                "runtime_ref": ref,
            },
        )

    orphan_projects = conn.execute(
        sa.text(
            """
            SELECT p.id, p.workspace_key, p.container_ref
            FROM projects p
            LEFT JOIN project_pods pp ON pp.project_id = p.id
            WHERE pp.id IS NULL
              AND p.container_ref IS NOT NULL
              AND p.container_ref != ''
            """
        )
    ).fetchall()
    import uuid

    for project_id, workspace_key, container_ref in orphan_projects:
        pod_id = f"pod_{uuid.uuid4().hex[:16]}"
        conn.execute(
            sa.text(
                """
                INSERT INTO project_pods (
                    id, project_id, workspace_key, status, desired_state, runtime_ref
                ) VALUES (
                    :id, :project_id, :workspace_key, 'pending', 'absent', :runtime_ref
                )
                """
            ),
            {
                "id": pod_id,
                "project_id": project_id,
                "workspace_key": workspace_key,
                "runtime_ref": container_ref,
            },
        )


def downgrade() -> None:
    op.drop_index("uq_project_pods_live_project", table_name="project_pods")
    op.drop_index("ix_project_pods_project_id", table_name="project_pods")
    op.drop_table("project_pods")
