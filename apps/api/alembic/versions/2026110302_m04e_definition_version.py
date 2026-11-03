"""M04.e definition/version foundation: owner keys, artifact proof, lifecycle.

Creates the three empty target tables owned by MIG-M04E-01:

* ``m04e_dynamic_app_definitions`` -- canonical definition identity, owner key
  ``(id, tenant_id)`` and company tenant FK;
* ``m04e_dynamic_app_versions`` -- same-definition version key
  ``(id, definition_id)``, deterministic ``(definition_id, manifest_version)``
  uniqueness and the composite definition identity FKs that mirror tenant/app;
* ``m04e_definition_version_promotion_evidence`` -- gate evidence records that
  can never enable a positive write.

Schema source of truth is
``prodavan/infrastructure/persistence/models/dynamic_app_definition_version.py``;
names, column order, types and check texts below mirror that metadata so an
``alembic check`` run after the freeze sees no drift.

Deliberate boundaries:

* the ``(current_version_id, id) -> (id, definition_id)`` FK is emitted as a
  named *late* constraint after both tables exist, ``DEFERRABLE INITIALLY
  DEFERRED``, so a definition and its current version can be inserted in either
  order inside one transaction without a cycle deadlock;
* published artifacts are protected by a database trigger as well as the ORM
  guard, because direct SQL and bulk DML bypass mapper events; the freeze holds
  for every lifecycle state other than ``draft``, and a frozen row cannot be
  rewound to ``draft``;
* no RLS policy, role, grant, writer, backfill or positive publication path is
  created here -- RLS is a separate revision (``2026110307``).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "2026110302"
down_revision = "2026110301"
branch_labels = None
depends_on = None


DEFINITION_TABLE = "m04e_dynamic_app_definitions"
VERSION_TABLE = "m04e_dynamic_app_versions"
PROMOTION_TABLE = "m04e_definition_version_promotion_evidence"

DEFINITION_INDEX = "ix_m04e_definition_tenant_lifecycle"
VERSION_INDEX = "ix_m04e_version_definition_lifecycle"
PROMOTION_INDEX = "ix_m04e_promotion_partition_time"

CURRENT_VERSION_FK = "fk_m04e_definition_current_version_same_definition"

ARTIFACT_GUARD_FUNCTION = "m04e_version_artifact_immutable"
ARTIFACT_GUARD_TRIGGER = "trg_m04e_version_artifact_immutable"

# Reason codes raised by the artifact guard. Redacted by construction: no row
# values, digests or payload fragments leak into the database or API error.
ARTIFACT_GUARD_REASON = "m04e_version_snapshot_immutable"
ARTIFACT_GUARD_REWIND_REASON = "m04e_version_snapshot_lifecycle_rewind"

_DEFINITION_STATES = "('draft', 'published', 'disabled', 'revoked')"
_VERSION_STATES = "('draft', 'published', 'disabled', 'revoked')"
_PROMOTION_STATES = "('blocked', 'quarantined')"

_CREATE_ARTIFACT_GUARD_FUNCTION = f"""
CREATE OR REPLACE FUNCTION {ARTIFACT_GUARD_FUNCTION}() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.lifecycle <> 'draft' THEN
            RAISE EXCEPTION '{ARTIFACT_GUARD_REASON}' USING ERRCODE = '23514';
        END IF;
        RETURN OLD;
    END IF;

    -- A row that left 'draft' is a frozen snapshot: publishing, disabling and
    -- revoking are lifecycle transitions, never a licence to rewrite the
    -- artifact. Without the rewind branch a published row could be moved back
    -- to 'draft' and then mutated, defeating the guard.
    IF OLD.lifecycle <> 'draft' THEN
        IF NEW.lifecycle = 'draft' THEN
            RAISE EXCEPTION '{ARTIFACT_GUARD_REWIND_REASON}' USING ERRCODE = '23514';
        END IF;

        IF NEW.id IS DISTINCT FROM OLD.id
            OR NEW.definition_id IS DISTINCT FROM OLD.definition_id
            OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
            OR NEW.app_id IS DISTINCT FROM OLD.app_id
            OR NEW.manifest_version IS DISTINCT FROM OLD.manifest_version
            OR NEW.artifact_digest IS DISTINCT FROM OLD.artifact_digest
            OR NEW.artifact_uri IS DISTINCT FROM OLD.artifact_uri
            OR NEW.manifest IS DISTINCT FROM OLD.manifest
            OR NEW.requested_capabilities IS DISTINCT FROM OLD.requested_capabilities
            OR NEW.source_snapshot_id IS DISTINCT FROM OLD.source_snapshot_id
            OR NEW.source_digest IS DISTINCT FROM OLD.source_digest
            OR NEW.created_at IS DISTINCT FROM OLD.created_at
            OR (
                NEW.lifecycle = 'published'
                AND NEW.published_at IS DISTINCT FROM OLD.published_at
            )
        THEN
            RAISE EXCEPTION '{ARTIFACT_GUARD_REASON}' USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    json_payload = postgresql.JSONB(astext_type=sa.Text())

    op.create_table(
        DEFINITION_TABLE,
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("app_id", sa.String(160), nullable=False),
        sa.Column("display_name", sa.String(260), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("scope_kind", sa.String(32), nullable=False),
        sa.Column("scope_id", sa.String(40), nullable=False),
        sa.Column("lifecycle", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("current_version_id", sa.String(80), nullable=True),
        sa.Column("source_snapshot_id", sa.String(200), nullable=False),
        sa.Column("source_digest", sa.String(71), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_m04e_definition_id_tenant"),
        sa.UniqueConstraint("id", "app_id", name="uq_m04e_definition_id_app"),
        sa.UniqueConstraint("tenant_id", "app_id", name="uq_m04e_definition_tenant_app"),
        sa.CheckConstraint("length(btrim(tenant_id)) > 0", name="ck_m04e_definition_tenant_nonempty"),
        sa.CheckConstraint("length(btrim(app_id)) > 0", name="ck_m04e_definition_app_id_format"),
        sa.CheckConstraint("length(btrim(display_name)) > 0", name="ck_m04e_definition_display_name_nonempty"),
        sa.CheckConstraint(f"lifecycle IN {_DEFINITION_STATES}", name="ck_m04e_definition_lifecycle"),
        sa.CheckConstraint("scope_kind IN ('tenant', 'cabinet', 'project')", name="ck_m04e_definition_scope_kind"),
        sa.CheckConstraint("scope_kind <> 'tenant' OR scope_id = tenant_id", name="ck_m04e_definition_tenant_scope_identity"),
        sa.CheckConstraint("scope_id <> ''", name="ck_m04e_definition_scope_nonempty"),
        sa.CheckConstraint("updated_at >= created_at", name="ck_m04e_definition_updated_at_order"),
        sa.CheckConstraint("lifecycle <> 'published' OR current_version_id IS NOT NULL", name="ck_m04e_definition_published_has_current"),
        sa.CheckConstraint("source_snapshot_id <> '' AND source_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_m04e_definition_source_proof"),
        sa.ForeignKeyConstraint(["tenant_id"], ["companies.id"], name="fk_m04e_definition_tenant", ondelete="RESTRICT"),
    )
    op.create_index(DEFINITION_INDEX, DEFINITION_TABLE, ["tenant_id", "lifecycle"])

    op.create_table(
        VERSION_TABLE,
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("definition_id", sa.String(80), nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("app_id", sa.String(160), nullable=False),
        sa.Column("manifest_version", sa.String(128), nullable=False),
        sa.Column("artifact_digest", sa.String(71), nullable=False),
        sa.Column("artifact_uri", sa.String(2048), nullable=True),
        sa.Column("manifest", json_payload, nullable=False),
        sa.Column("requested_capabilities", json_payload, nullable=False),
        sa.Column("lifecycle", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("source_snapshot_id", sa.String(200), nullable=False),
        sa.Column("source_digest", sa.String(71), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("definition_id", "manifest_version", name="uq_m04e_version_definition_manifest"),
        sa.UniqueConstraint("id", "definition_id", name="uq_m04e_version_id_definition"),
        sa.UniqueConstraint("definition_id", "artifact_digest", name="uq_m04e_version_definition_artifact"),
        sa.UniqueConstraint("id", "definition_id", "artifact_digest", name="uq_m04e_version_id_definition_artifact"),
        sa.CheckConstraint("manifest_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+(-[0-9A-Za-z.-]+)?$'", name="ck_m04e_version_manifest_semver"),
        sa.CheckConstraint("artifact_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_m04e_version_artifact_sha256"),
        sa.CheckConstraint(f"lifecycle IN {_VERSION_STATES}", name="ck_m04e_version_lifecycle"),
        sa.CheckConstraint("COALESCE(manifest->>'protocol' = '1.0', false)", name="ck_m04e_version_manifest_protocol"),
        sa.CheckConstraint("COALESCE(manifest->>'version' = manifest_version, false)", name="ck_m04e_version_manifest_version_match"),
        sa.CheckConstraint("COALESCE(manifest->>'appId' = app_id, false)", name="ck_m04e_version_manifest_app_id_match"),
        sa.CheckConstraint(
            "COALESCE(manifest #>> '{entrypoints,plugin,artifact,sha256}' IS NULL OR manifest #>> '{entrypoints,plugin,artifact,sha256}' = substring(artifact_digest FROM 8), false)",
            name="ck_m04e_version_manifest_artifact_digest_match",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(manifest) = 'object' AND COALESCE(jsonb_typeof(manifest->'appId') = 'string' AND manifest->>'appId' ~ '^[a-z][a-z0-9-]{2,63}$', false) AND COALESCE(jsonb_typeof(manifest->'displayName') = 'string' AND length(btrim(manifest->>'displayName')) > 0, false) AND COALESCE(jsonb_typeof(manifest->'entrypoints') = 'object' AND manifest->'entrypoints' <> '{}'::jsonb, false) AND COALESCE(jsonb_typeof(manifest->'requestedCapabilities') = 'array', false)",
            name="ck_m04e_version_manifest_shape",
        ),
        sa.CheckConstraint(
            "COALESCE(jsonb_typeof(manifest->'requestedCapabilities') = 'array', false) AND requested_capabilities = manifest->'requestedCapabilities'",
            name="ck_m04e_version_requested_capabilities_match",
        ),
        sa.CheckConstraint("lifecycle = 'published' OR published_at IS NULL", name="ck_m04e_version_published_at_lifecycle"),
        sa.CheckConstraint("lifecycle <> 'published' OR published_at IS NOT NULL", name="ck_m04e_version_published_lifecycle_timestamp"),
        sa.CheckConstraint("source_snapshot_id <> '' AND source_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_m04e_version_source_proof"),
        sa.ForeignKeyConstraint(
            ["definition_id", "tenant_id"],
            [f"{DEFINITION_TABLE}.id", f"{DEFINITION_TABLE}.tenant_id"],
            name="fk_m04e_version_definition_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["definition_id", "app_id"],
            [f"{DEFINITION_TABLE}.id", f"{DEFINITION_TABLE}.app_id"],
            name="fk_m04e_version_definition_app",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(VERSION_INDEX, VERSION_TABLE, ["definition_id", "lifecycle"])

    # Database-side artifact immutability. The ORM `before_update` guard does not
    # see direct SQL, bulk DML or another writer process, so a non-draft version
    # snapshot is protected at the storage boundary as well.
    op.execute(_CREATE_ARTIFACT_GUARD_FUNCTION)
    op.execute(
        f"CREATE TRIGGER {ARTIFACT_GUARD_TRIGGER} "
        f"BEFORE UPDATE OR DELETE ON {VERSION_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION {ARTIFACT_GUARD_FUNCTION}()"
    )

    op.create_table(
        PROMOTION_TABLE,
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("definition_id", sa.String(80), nullable=False),
        sa.Column("revision_id", sa.String(80), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("source_snapshot_id", sa.String(200), nullable=False),
        sa.Column("source_digest", sa.String(71), nullable=False),
        sa.Column("target_digest", sa.String(71), nullable=False),
        sa.Column("valid_count", sa.Integer(), nullable=False),
        sa.Column("archived_count", sa.Integer(), nullable=False),
        sa.Column("quarantined_count", sa.Integer(), nullable=False),
        sa.Column("current_version_resolution_digest", sa.String(71), nullable=False),
        sa.Column("artifact_equality_digest", sa.String(71), nullable=False),
        sa.Column("gate_evidence", json_payload, nullable=False),
        sa.Column("canonical_positive_writes", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("reviewer", sa.String(200), nullable=True),
        sa.Column("previous_revision_id", sa.String(80), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "definition_id", "revision_id", name="uq_m04e_promotion_revision"),
        sa.CheckConstraint(f"outcome IN {_PROMOTION_STATES}", name="ck_m04e_promotion_outcome"),
        sa.CheckConstraint("canonical_positive_writes = false", name="ck_m04e_no_positive_writes"),
        sa.CheckConstraint("source_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_m04e_promotion_source_sha256"),
        sa.CheckConstraint("target_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_m04e_promotion_target_sha256"),
        sa.CheckConstraint("current_version_resolution_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_m04e_promotion_current_resolution_sha256"),
        sa.CheckConstraint("artifact_equality_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_m04e_promotion_artifact_equality_sha256"),
        sa.CheckConstraint("valid_count >= 0 AND archived_count >= 0 AND quarantined_count >= 0", name="ck_m04e_promotion_counts_nonnegative"),
        sa.ForeignKeyConstraint(
            ["definition_id", "tenant_id"],
            [f"{DEFINITION_TABLE}.id", f"{DEFINITION_TABLE}.tenant_id"],
            name="fk_m04e_promotion_definition_tenant",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(PROMOTION_INDEX, PROMOTION_TABLE, ["tenant_id", "definition_id", "recorded_at"])

    # Late cycle-breaking constraint: emitted only after both keys exist. The
    # deferral makes the definition/current-version cycle insertable in one
    # transaction; the composite pair rejects a version of a different
    # definition or a different tenant.
    op.create_foreign_key(
        CURRENT_VERSION_FK,
        DEFINITION_TABLE,
        VERSION_TABLE,
        ["current_version_id", "id"],
        ["id", "definition_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
        use_alter=True,
    )


def downgrade() -> None:
    # Reverse order; only objects created by this revision are removed.
    op.drop_constraint(CURRENT_VERSION_FK, DEFINITION_TABLE, type_="foreignkey")

    op.drop_index(PROMOTION_INDEX, table_name=PROMOTION_TABLE)
    op.drop_table(PROMOTION_TABLE)

    op.execute(f"DROP TRIGGER IF EXISTS {ARTIFACT_GUARD_TRIGGER} ON {VERSION_TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {ARTIFACT_GUARD_FUNCTION}()")

    op.drop_index(VERSION_INDEX, table_name=VERSION_TABLE)
    op.drop_table(VERSION_TABLE)

    op.drop_index(DEFINITION_INDEX, table_name=DEFINITION_TABLE)
    op.drop_table(DEFINITION_TABLE)
