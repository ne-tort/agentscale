"""Enable child_may_edit on global mod_equipment project binds."""

import sqlalchemy as sa
from alembic import op

revision = "2026091407"
down_revision = "2026091406"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Equipment agent needs to write request_lines / found_offers into shared SoT.
    op.execute(
        sa.text(
            """
            UPDATE module_project_bindings
            SET child_may_edit = true
            WHERE module_id = 'mod_equipment'
              AND bind_kind = 'global'
              AND child_may_edit = false
            """
        )
    )


def downgrade() -> None:
    pass
