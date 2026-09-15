"""Add employee_composer_drafts for chat input cache."""

import sqlalchemy as sa
from alembic import op

revision = "2026091501"
down_revision = "2026091408"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "employee_composer_drafts",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("employee_id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("session_id", sa.String(length=40), nullable=True),
        sa.Column("scope_key", sa.String(length=80), nullable=False),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("employee_id", "scope_key", name="uq_composer_draft_employee_scope"),
    )
    op.create_index("ix_employee_composer_drafts_employee_id", "employee_composer_drafts", ["employee_id"])
    op.create_index("ix_employee_composer_drafts_project_id", "employee_composer_drafts", ["project_id"])
    op.create_index("ix_employee_composer_drafts_session_id", "employee_composer_drafts", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_employee_composer_drafts_session_id", table_name="employee_composer_drafts")
    op.drop_index("ix_employee_composer_drafts_project_id", table_name="employee_composer_drafts")
    op.drop_index("ix_employee_composer_drafts_employee_id", table_name="employee_composer_drafts")
    op.drop_table("employee_composer_drafts")
