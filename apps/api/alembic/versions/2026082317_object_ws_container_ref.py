"""Normalize legacy local-ws container_ref → object-ws (C-MATERIALIZE)."""

from alembic import op

revision = "2026082317"
down_revision = "2026082316"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Dual-read already accepts local-ws; rewrite stored refs to canonical object-ws.
    # 'local-ws:' is 9 chars → substr from 10.
    op.execute(
        """
        UPDATE projects
        SET container_ref = 'object-ws:' || substr(container_ref, 10)
        WHERE container_ref LIKE 'local-ws:%'
        """
    )


def downgrade() -> None:
    # 'object-ws:' is 10 chars → substr from 11.
    op.execute(
        """
        UPDATE projects
        SET container_ref = 'local-ws:' || substr(container_ref, 11)
        WHERE container_ref LIKE 'object-ws:%'
        """
    )
