"""AI key scope cascade — employee company_id + project bindings."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026082902"
down_revision = "2026082901"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_employee_ai_key", "employee_ai_key_bindings", type_="unique")
    op.add_column(
        "employee_ai_key_bindings",
        sa.Column("company_id", sa.String(length=40), nullable=True),
    )
    op.execute(
        """
        UPDATE employee_ai_key_bindings e
        SET company_id = (
            SELECT m.company_id
            FROM memberships m
            WHERE m.employee_id = e.employee_id
            ORDER BY m.created_at
            LIMIT 1
        )
        """
    )
    op.execute("DELETE FROM employee_ai_key_bindings WHERE company_id IS NULL")
    op.alter_column("employee_ai_key_bindings", "company_id", nullable=False)
    op.create_foreign_key(
        "fk_employee_ai_key_bindings_company_id",
        "employee_ai_key_bindings",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_employee_ai_key_company",
        "employee_ai_key_bindings",
        ["company_id", "employee_id", "key_id"],
    )

    op.create_table(
        "project_ai_key_bindings",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(length=40),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.String(length=40),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "key_id",
            sa.String(length=40),
            sa.ForeignKey("ai_provider_keys.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "key_id", name="uq_project_ai_key"),
    )


def downgrade() -> None:
    op.drop_table("project_ai_key_bindings")
    op.drop_constraint("uq_employee_ai_key_company", "employee_ai_key_bindings", type_="unique")
    op.drop_constraint("fk_employee_ai_key_bindings_company_id", "employee_ai_key_bindings", type_="foreignkey")
    op.drop_column("employee_ai_key_bindings", "company_id")
    op.create_unique_constraint(
        "uq_employee_ai_key",
        "employee_ai_key_bindings",
        ["employee_id", "key_id"],
    )
