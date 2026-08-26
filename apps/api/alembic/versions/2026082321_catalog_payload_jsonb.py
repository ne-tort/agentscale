"""Align reference_catalog_entries.payload to JSONB (matches ORM / peer tables).

Idempotent for envs that already created the column as JSON via 2026082320.
"""

from alembic import op

revision = "2026082321"
down_revision = "2026082320"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE reference_catalog_entries "
        "ALTER COLUMN payload DROP DEFAULT"
    )
    op.execute(
        "ALTER TABLE reference_catalog_entries "
        "ALTER COLUMN payload TYPE jsonb USING payload::jsonb"
    )
    op.execute(
        "ALTER TABLE reference_catalog_entries "
        "ALTER COLUMN payload SET DEFAULT '{}'::jsonb"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE reference_catalog_entries "
        "ALTER COLUMN payload DROP DEFAULT"
    )
    op.execute(
        "ALTER TABLE reference_catalog_entries "
        "ALTER COLUMN payload TYPE json USING payload::json"
    )
    op.execute(
        "ALTER TABLE reference_catalog_entries "
        "ALTER COLUMN payload SET DEFAULT '{}'::json"
    )
