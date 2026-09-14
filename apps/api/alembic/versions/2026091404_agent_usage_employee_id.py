"""Add agent_usage.employee_id for Metrics BC attribution."""

import sqlalchemy as sa
from alembic import op

revision = "2026091404"
down_revision = "2026091403"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_usage",
        sa.Column("employee_id", sa.String(length=40), nullable=True),
    )
    op.create_index("ix_agent_usage_employee_id", "agent_usage", ["employee_id"])
    op.create_foreign_key(
        "fk_agent_usage_employee_id",
        "agent_usage",
        "employees",
        ["employee_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_agent_usage_employee_id", "agent_usage", type_="foreignkey")
    op.drop_index("ix_agent_usage_employee_id", table_name="agent_usage")
    op.drop_column("agent_usage", "employee_id")
