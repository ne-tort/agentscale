"""Module company_grant_scope — product modules visible to all companies."""

import sqlalchemy as sa
from alembic import op

revision = "2026082714"
down_revision = "2026082713"
branch_labels = None
depends_on = None

_PRODUCT_MODULE_IDS = ("mod_prompts", "mod_mcp", "mod_files")


def upgrade() -> None:
    op.add_column(
        "modules",
        sa.Column(
            "company_grant_scope",
            sa.String(length=32),
            server_default="selected",
            nullable=False,
        ),
    )
    conn = op.get_bind()
    for module_id in _PRODUCT_MODULE_IDS:
        conn.execute(
            sa.text(
                "UPDATE modules SET company_grant_scope = 'all' WHERE id = :id"
            ),
            {"id": module_id},
        )


def downgrade() -> None:
    op.drop_column("modules", "company_grant_scope")
