"""Deploy smoke marker — data-only revision to verify migrate.sh on rollout."""

from alembic import op

revision = "2026082319"
down_revision = "2026082318"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO stub_meta (key, value)
        VALUES ('migration_smoke', '2026082319')
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM stub_meta WHERE key = 'migration_smoke'")
