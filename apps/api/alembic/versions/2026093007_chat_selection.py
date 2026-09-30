"""chat selection column"""

import sqlalchemy as sa

from alembic import op

revision = "2026093007"
down_revision = "2026093006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "employee_project_selections",
        sa.Column("chat_session_id", sa.String(length=40), nullable=True),
    )
    op.create_foreign_key(
        "fk_emp_proj_sel_chat_session",
        "employee_project_selections",
        "agent_sessions",
        ["chat_session_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_emp_proj_sel_chat_session", "employee_project_selections", type_="foreignkey"
    )
    op.drop_column("employee_project_selections", "chat_session_id")
