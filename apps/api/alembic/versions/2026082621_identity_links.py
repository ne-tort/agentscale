"""identity_links — optional Employee ↔ IdP provider audit (not authz)."""

import sqlalchemy as sa

from alembic import op

revision = "2026082621"
down_revision = "2026082620"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identity_links",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("employee_id", sa.String(length=40), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_subject", sa.String(length=255), nullable=False),
        sa.Column(
            "linked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name="fk_identity_links_employee_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "provider",
            "provider_subject",
            name="uq_identity_link_provider_subject",
        ),
    )
    op.create_index("ix_identity_links_employee_id", "identity_links", ["employee_id"])


def downgrade() -> None:
    op.drop_index("ix_identity_links_employee_id", table_name="identity_links")
    op.drop_table("identity_links")
