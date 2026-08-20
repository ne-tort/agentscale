"""Drop hard-coded electronics-procurement S4B PG trigger (G10).

S4B is gated by cabinet capabilities / SPI; profile allowlist belongs in app code.
"""

from alembic import op

revision = "2026082101"
down_revision = "2026082001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_cabinets_s4b ON tenants.cabinets")
    op.execute("DROP FUNCTION IF EXISTS tenants.enforce_s4b_profile()")


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION tenants.enforce_s4b_profile()
        RETURNS trigger AS $$
        BEGIN
            IF (NEW.capabilities->'integrations'->'s4b'->>'enabled')::boolean IS TRUE
               AND COALESCE(NEW.profile_id, '') <> 'electronics-procurement' THEN
                RAISE EXCEPTION 'CAPABILITY_FORBIDDEN: s4b only for electronics-procurement';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_cabinets_s4b
        BEFORE INSERT OR UPDATE ON tenants.cabinets
        FOR EACH ROW EXECUTE FUNCTION tenants.enforce_s4b_profile()
        """
    )
