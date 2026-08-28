"""Allow new live pod after failed — partial unique index excludes failed."""

import sqlalchemy as sa

from alembic import op

revision = "2026082820"
down_revision = "2026082819"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("uq_project_pods_live_project", table_name="project_pods")
    op.create_index(
        "uq_project_pods_live_project",
        "project_pods",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text(
            "project_id IS NOT NULL AND status NOT IN ('terminated', 'failed')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_project_pods_live_project", table_name="project_pods")
    op.create_index(
        "uq_project_pods_live_project",
        "project_pods",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("project_id IS NOT NULL AND status NOT IN ('terminated')"),
    )
