"""Module company grants + owner_scope on modules."""

import sqlalchemy as sa
from alembic import op

revision = "2026082705"
down_revision = "2026082704"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "modules",
        sa.Column("owner_scope", sa.String(length=32), server_default="platform", nullable=False),
    )
    op.add_column(
        "modules",
        sa.Column(
            "owner_company_id",
            sa.String(length=40),
            sa.ForeignKey("companies.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_modules_owner_company_id", "modules", ["owner_company_id"])

    op.create_table(
        "module_company_grants",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("module_id", sa.String(length=40), sa.ForeignKey("modules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", sa.String(length=40), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("module_id", "company_id", name="uq_module_company_grant"),
    )
    op.create_index("ix_module_company_grants_company_id", "module_company_grants", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_module_company_grants_company_id", table_name="module_company_grants")
    op.drop_table("module_company_grants")
    op.drop_index("ix_modules_owner_company_id", table_name="modules")
    op.drop_column("modules", "owner_company_id")
    op.drop_column("modules", "owner_scope")
