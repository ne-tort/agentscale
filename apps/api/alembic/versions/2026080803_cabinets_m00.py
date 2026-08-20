"""M00 cabinets schema extensions."""

from alembic import op

revision = "2026080803"
down_revision = "2026080802"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE tenants.cabinet_profiles (
            id TEXT PRIMARY KEY,
            version TEXT NOT NULL,
            display_name TEXT NOT NULL,
            description TEXT,
            capabilities_schema JSONB NOT NULL DEFAULT '{}',
            pack_checksum TEXT NOT NULL DEFAULT '',
            deprecated BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        INSERT INTO tenants.cabinet_profiles (id, version, display_name, description, capabilities_schema)
        VALUES
        (
            'electronics-procurement',
            '1.0.0',
            'Закупки электроники',
            'Спеки, S4B, каталоги, КП',
            '{"capabilities":["procurement.kp","procurement.equipment","procurement.s4b","procurement.pipeline"],"s4b"\\:true}'::jsonb
        ),
        (
            'generic-assistant',
            '1.0.0',
            'Универсальный ассистент',
            'Базовый ассистент без S4B',
            '{"capabilities":["agent.session"],"s4b"\\:false}'::jsonb
        )
    """)

    op.execute("""
        ALTER TABLE tenants.cabinets
            ADD COLUMN profile_id TEXT REFERENCES tenants.cabinet_profiles(id),
            ADD COLUMN profile_version TEXT,
            ADD COLUMN status TEXT NOT NULL DEFAULT 'active',
            ADD COLUMN capabilities JSONB NOT NULL DEFAULT '{}',
            ADD COLUMN metadata JSONB NOT NULL DEFAULT '{}',
            ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            ADD COLUMN archived_at TIMESTAMPTZ,
            ADD COLUMN created_by UUID REFERENCES tenants.users(id)
    """)

    op.execute("""
        UPDATE tenants.cabinets
        SET status = 'active', capabilities = '{}'
        WHERE profile_id IS NULL
    """)

    op.execute("""
        CREATE TABLE tenants.cabinet_seed_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            cabinet_id UUID NOT NULL REFERENCES tenants.cabinets(id) ON DELETE CASCADE,
            pack_version TEXT NOT NULL,
            step TEXT NOT NULL,
            status TEXT NOT NULL,
            error_detail JSONB,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ,
            UNIQUE (cabinet_id, pack_version, step)
        )
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION tenants.enforce_s4b_profile()
        RETURNS TRIGGER AS $$
        BEGIN
            IF (NEW.capabilities->'integrations'->'s4b'->>'enabled')::boolean IS TRUE
               AND COALESCE(NEW.profile_id, '') <> 'electronics-procurement' THEN
                RAISE EXCEPTION 'CAPABILITY_FORBIDDEN: s4b only for electronics-procurement';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    op.execute("""
        CREATE TRIGGER trg_cabinets_s4b
        BEFORE INSERT OR UPDATE ON tenants.cabinets
        FOR EACH ROW EXECUTE FUNCTION tenants.enforce_s4b_profile()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_cabinets_s4b ON tenants.cabinets")
    op.execute("DROP FUNCTION IF EXISTS tenants.enforce_s4b_profile()")
    op.execute("DROP TABLE IF EXISTS tenants.cabinet_seed_runs")
    op.execute("""
        ALTER TABLE tenants.cabinets
            DROP COLUMN IF EXISTS profile_id,
            DROP COLUMN IF EXISTS profile_version,
            DROP COLUMN IF EXISTS status,
            DROP COLUMN IF EXISTS capabilities,
            DROP COLUMN IF EXISTS metadata,
            DROP COLUMN IF EXISTS updated_at,
            DROP COLUMN IF EXISTS archived_at,
            DROP COLUMN IF EXISTS created_by
    """)
    op.execute("DROP TABLE IF EXISTS tenants.cabinet_profiles")
