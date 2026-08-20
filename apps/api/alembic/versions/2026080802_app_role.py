"""Create non-superuser app role for RLS enforcement."""

from alembic import op

revision = "2026080802"
down_revision = "2026080801"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'prodavan_app') THEN
                CREATE ROLE prodavan_app LOGIN PASSWORD 'prodavan';
            END IF;
        END $$
    """)
    op.execute("GRANT USAGE ON SCHEMA tenants TO prodavan_app")
    op.execute("GRANT ALL ON ALL TABLES IN SCHEMA tenants TO prodavan_app")
    op.execute("GRANT ALL ON ALL SEQUENCES IN SCHEMA tenants TO prodavan_app")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA tenants "
        "GRANT ALL ON TABLES TO prodavan_app"
    )


def downgrade() -> None:
    op.execute("DROP ROLE IF EXISTS prodavan_app")
