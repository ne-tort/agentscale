"""M01 projects schema."""

from alembic import op

revision = "2026080804"
down_revision = "2026080803"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE tenants.projects (
            id TEXT PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants.tenants(id) ON DELETE CASCADE,
            cabinet_id UUID NOT NULL REFERENCES tenants.cabinets(id) ON DELETE CASCADE,
            slug TEXT NOT NULL,
            display_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            workspace_key TEXT NOT NULL UNIQUE,
            last_opened_at TIMESTAMPTZ,
            created_by UUID NOT NULL REFERENCES tenants.users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            archived_at TIMESTAMPTZ,
            UNIQUE (cabinet_id, slug)
        )
    """)
    op.execute(
        "CREATE INDEX idx_projects_cabinet ON tenants.projects (cabinet_id, status)"
    )
    op.execute(
        "CREATE INDEX idx_projects_workspace ON tenants.projects (workspace_key)"
    )

    op.execute("ALTER TABLE tenants.projects ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenants.projects FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY projects_cabinet ON tenants.projects
        FOR ALL
        USING (
            tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
            AND cabinet_id = nullif(current_setting('app.cabinet_id', true), '')::uuid
        )
        WITH CHECK (
            tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
            AND cabinet_id = nullif(current_setting('app.cabinet_id', true), '')::uuid
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tenants.projects CASCADE")
