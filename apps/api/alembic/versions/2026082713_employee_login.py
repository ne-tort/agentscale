"""Employee login + contact_email for editable employee credentials."""

import sqlalchemy as sa
from alembic import op

revision = "2026082713"
down_revision = "2026082712"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "employees",
        sa.Column("login", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "employees",
        sa.Column("contact_email", sa.String(length=320), nullable=True),
    )

    op.execute(
        """
        UPDATE employees
        SET login = split_part(email, '@', 1)
        WHERE login IS NULL
        """
    )
    op.execute(
        """
        UPDATE employees
        SET login = id
        WHERE login IS NULL OR login = '' OR length(login) < 3
        """
    )
    op.execute(
        """
        UPDATE employees e
        SET login = e.id
        FROM (
            SELECT id,
                   ROW_NUMBER() OVER (PARTITION BY login ORDER BY id) AS rn
            FROM employees
        ) ranked
        WHERE e.id = ranked.id AND ranked.rn > 1
        """
    )

    op.create_index("ix_employees_login", "employees", ["login"], unique=True)
    op.alter_column("employees", "login", nullable=False)


def downgrade() -> None:
    op.drop_index("ix_employees_login", table_name="employees")
    op.drop_column("employees", "contact_email")
    op.drop_column("employees", "login")
