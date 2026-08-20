"""Initial tenants schema and RLS policies."""

from alembic import op

revision = "2026080801"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS tenants")

    op.execute("""
        CREATE TABLE tenants.tenants (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            slug TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            plan TEXT NOT NULL DEFAULT 'starter',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at TIMESTAMPTZ
        )
    """)

    op.execute("""
        CREATE TABLE tenants.users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            mfa_secret TEXT,
            mfa_enabled BOOLEAN NOT NULL DEFAULT false,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE tenants.cabinets (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants.tenants(id) ON DELETE CASCADE,
            slug TEXT NOT NULL,
            display_name TEXT NOT NULL,
            timezone TEXT NOT NULL DEFAULT 'UTC',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, slug)
        )
    """)

    op.execute("""
        CREATE TABLE tenants.tenant_memberships (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants.tenants(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES tenants.users(id) ON DELETE CASCADE,
            tenant_role TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, user_id)
        )
    """)

    op.execute("""
        CREATE TABLE tenants.cabinet_memberships (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            cabinet_id UUID NOT NULL REFERENCES tenants.cabinets(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES tenants.users(id) ON DELETE CASCADE,
            cabinet_role TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (cabinet_id, user_id)
        )
    """)

    op.execute("""
        CREATE TABLE tenants.refresh_tokens (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES tenants.users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            revoked_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute(
        "CREATE INDEX idx_tenant_memberships_tenant_user "
        "ON tenants.tenant_memberships (tenant_id, user_id)"
    )
    op.execute(
        "CREATE INDEX idx_cabinet_memberships_cabinet_user "
        "ON tenants.cabinet_memberships (cabinet_id, user_id)"
    )
    op.execute("CREATE INDEX idx_cabinets_tenant ON tenants.cabinets (tenant_id)")

    # RLS on cabinet-scoped tables; tenant_memberships RLS deferred to I2 (auth bootstrap)
    for table, policy_sql in (
        (
            "cabinets",
            """
            CREATE POLICY tenant_isolation ON tenants.cabinets
            FOR ALL
            USING (
                tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
            )
            WITH CHECK (
                tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
            )
            """,
        ),
        (
            "cabinet_memberships",
            """
            CREATE POLICY cabinet_membership_tenant ON tenants.cabinet_memberships
            FOR ALL
            USING (
                cabinet_id IN (
                    SELECT id FROM tenants.cabinets
                    WHERE tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
                )
            )
            WITH CHECK (
                cabinet_id IN (
                    SELECT id FROM tenants.cabinets
                    WHERE tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
                )
            )
            """,
        ),
    ):
        op.execute(f"ALTER TABLE tenants.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE tenants.{table} FORCE ROW LEVEL SECURITY")
        op.execute(policy_sql)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tenants.refresh_tokens CASCADE")
    op.execute("DROP TABLE IF EXISTS tenants.cabinet_memberships CASCADE")
    op.execute("DROP TABLE IF EXISTS tenants.tenant_memberships CASCADE")
    op.execute("DROP TABLE IF EXISTS tenants.cabinets CASCADE")
    op.execute("DROP TABLE IF EXISTS tenants.users CASCADE")
    op.execute("DROP TABLE IF EXISTS tenants.tenants CASCADE")
    op.execute("DROP SCHEMA IF EXISTS tenants CASCADE")
