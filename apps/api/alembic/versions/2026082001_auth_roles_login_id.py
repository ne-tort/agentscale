"""Auth roles: login_id, company fields, platform.admin."""

from alembic import op

revision = "2026082001"
down_revision = "2026080804"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE tenants.users
            ADD COLUMN IF NOT EXISTS login_id TEXT,
            ADD COLUMN IF NOT EXISTS company_name TEXT,
            ADD COLUMN IF NOT EXISTS contact_person TEXT,
            ADD COLUMN IF NOT EXISTS phone TEXT,
            ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'user',
            ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ
    """)

    op.execute("""
        UPDATE tenants.users
        SET
            login_id = COALESCE(login_id, lower(split_part(email, '@', 1))),
            company_name = COALESCE(company_name, display_name),
            contact_person = COALESCE(contact_person, display_name)
        WHERE login_id IS NULL OR company_name IS NULL
    """)

    op.execute("""
        WITH dups AS (
            SELECT id, login_id,
                   ROW_NUMBER() OVER (PARTITION BY login_id ORDER BY created_at) AS rn
            FROM tenants.users
        )
        UPDATE tenants.users u
        SET login_id = u.login_id || '-' || substr(u.id::text, 1, 8)
        FROM dups
        WHERE u.id = dups.id AND dups.rn > 1
    """)

    op.execute("ALTER TABLE tenants.users ALTER COLUMN login_id SET NOT NULL")
    op.execute("ALTER TABLE tenants.users ALTER COLUMN company_name SET NOT NULL")

    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_users_login_id'
            ) THEN
                ALTER TABLE tenants.users ADD CONSTRAINT uq_users_login_id UNIQUE (login_id);
            END IF;
        END $$
    """)

    op.execute("ALTER TABLE tenants.users ALTER COLUMN email DROP NOT NULL")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'users_email_key' AND conrelid = 'tenants.users'::regclass
            ) THEN
                ALTER TABLE tenants.users DROP CONSTRAINT users_email_key;
            END IF;
        END $$
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_not_null
        ON tenants.users (lower(email))
        WHERE email IS NOT NULL AND email <> ''
    """)

    # Cross-tenant aggregates for platform.admin (bypass table RLS)
    op.execute("""
        CREATE OR REPLACE FUNCTION tenants.platform_stats()
        RETURNS TABLE (
            users_total bigint,
            tenants_total bigint,
            cabinets_total bigint,
            projects_total bigint
        )
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = tenants, pg_temp
        AS $$
            SELECT
                (SELECT COUNT(*) FROM tenants.users WHERE role = 'user'),
                (SELECT COUNT(*) FROM tenants.tenants WHERE slug <> '_platform'),
                (SELECT COUNT(*) FROM tenants.cabinets),
                (SELECT COUNT(*) FROM tenants.projects)
        $$
    """)
    op.execute("GRANT EXECUTE ON FUNCTION tenants.platform_stats() TO prodavan_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS tenants.platform_stats()")
    op.execute("DROP INDEX IF EXISTS tenants.uq_users_email_not_null")
    op.execute("ALTER TABLE tenants.users DROP CONSTRAINT IF EXISTS uq_users_login_id")
    op.execute("""
        ALTER TABLE tenants.users
            DROP COLUMN IF EXISTS login_id,
            DROP COLUMN IF EXISTS company_name,
            DROP COLUMN IF EXISTS contact_person,
            DROP COLUMN IF EXISTS phone,
            DROP COLUMN IF EXISTS role,
            DROP COLUMN IF EXISTS deleted_at
    """)
