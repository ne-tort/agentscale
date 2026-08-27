"""Platform bootstrap table + cabinet company_grant_scope."""

import sqlalchemy as sa
from alembic import op

revision = "2026082710"
down_revision = "2026082709"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_bootstrap",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "cabinet_instances",
        sa.Column(
            "company_grant_scope",
            sa.String(length=32),
            server_default="selected",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("cabinet_instances", "company_grant_scope")
    op.drop_table("platform_bootstrap")
