"""Resources, runtime revisions and durable materialization attempt/fence/audit.

Creates the six tables that already exist as registered ORM rows —
``resources``, ``resource_versions``, ``resource_bindings``,
``capability_bundles``, ``project_runtime_revisions`` and
``materialization_entries`` — plus the three durable objects that close the
MIG-RES-02 persistence blockers:

* ``runtime_revision_heads`` — resource-wide CAS generation. The conditional
  update on this row fences a plan even when the plan contains zero
  materialization entries, which ``materialization_entries.generation`` cannot
  do because it is per path.
* ``runtime_materialization_attempts`` — durable attempt/receipt history per
  resource (many attempts per revision, one row per claimed generation) with an
  idempotency key, observed generation, outcome and receipt digest.
* ``runtime_materialization_audit`` — append-only redacted audit (reason code
  and correlation id only), protected by a trigger that rejects UPDATE, DELETE
  and TRUNCATE.

Out of scope by design: row-level security, tenant context and role privileges
(``GRANT INSERT`` only on the audit table). Those belong to revision
``2026110307``; this revision creates no policy and no grant.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "2026110306"
down_revision = "2026110305"
branch_labels = None
depends_on = None

_AUDIT_APPEND_ONLY_FUNCTION = "prevent_runtime_materialization_audit_mutation"


def upgrade() -> None:
    json_payload = postgresql.JSONB(astext_type=sa.Text())

    op.create_table(
        "resources",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("slug", sa.String(160), nullable=False),
        sa.Column("title", sa.String(260), nullable=False),
        sa.Column("scope_kind", sa.String(32), nullable=False),
        sa.Column("scope_id", sa.String(80), nullable=False),
        sa.Column("metadata", json_payload, nullable=False, server_default="{}"),
        sa.Column("active_version", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_kind", "scope_id", "kind", "slug", name="uq_resource_scope_kind_slug"),
    )
    op.create_index("ix_resources_scope", "resources", ["scope_kind", "scope_id"])

    op.create_table(
        "resource_versions",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.String(40), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("payload", json_payload, nullable=False, server_default="{}"),
        sa.Column("checksum", sa.String(128), nullable=True),
        sa.Column("created_by", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["resource_id"], ["resources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resource_id", "number", name="uq_resource_version_number"),
    )
    op.create_index(
        "ix_resource_versions_resource_status",
        "resource_versions",
        ["resource_id", "status"],
    )

    op.create_table(
        "resource_bindings",
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.String(40), nullable=False),
        sa.Column("target_scope_kind", sa.String(32), nullable=False),
        sa.Column("target_scope_id", sa.String(80), nullable=False),
        sa.Column("binding_kind", sa.String(16), nullable=False, server_default="global"),
        sa.Column("child_may_edit", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("version_number", sa.Integer(), nullable=True),
        sa.Column(
            "project_ids",
            postgresql.ARRAY(sa.String(80)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("session_id", sa.String(80), nullable=True),
        sa.Column("created_by", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["resource_id"], ["resources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "resource_id",
            "target_scope_kind",
            "target_scope_id",
            "session_id",
            name="uq_resource_binding_target",
        ),
    )
    op.create_index(
        "ix_resource_bindings_target",
        "resource_bindings",
        ["target_scope_kind", "target_scope_id", "session_id"],
    )
    op.create_index(
        "uq_resource_binding_target_without_session",
        "resource_bindings",
        ["resource_id", "target_scope_kind", "target_scope_id"],
        unique=True,
        postgresql_where=sa.text("session_id IS NULL"),
    )

    op.create_table(
        "capability_bundles",
        sa.Column("id", sa.String(128), nullable=False),
        sa.Column("manifest", json_payload, nullable=False, server_default="{}"),
        sa.Column("digest", sa.String(128), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "project_runtime_revisions",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(40), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("bundle_id", sa.String(128), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="planned"),
        sa.Column("applied_revision_id", sa.String(64), nullable=True),
        sa.Column("pod_id", sa.String(64), nullable=True),
        sa.Column("pod_uid", sa.String(128), nullable=True),
        sa.Column("hydrate_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bridge_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("runtime_image_digest", sa.String(256), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("diagnostics", json_payload, nullable=True),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("source_event", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["bundle_id"], ["capability_bundles.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "revision", name="uq_project_runtime_revision_number"),
        sa.CheckConstraint(
            "state IN ('planned', 'validated', 'applying', 'ready', 'failed', 'superseded')",
            name="ck_project_runtime_revision_state",
        ),
    )
    op.create_index(
        "ix_project_runtime_revisions_project_state_revision",
        "project_runtime_revisions",
        ["project_id", "state", sa.text("revision DESC")],
    )

    op.create_table(
        "materialization_entries",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("revision_id", sa.String(64), nullable=False),
        sa.Column("workspace_path", sa.String(1024), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("operation_digest", sa.String(128), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="planned"),
        sa.Column("content_hash", sa.String(128), nullable=True),
        sa.Column("source_asset_id", sa.String(128), nullable=True),
        sa.Column("source_capability_id", sa.String(128), nullable=True),
        sa.Column("provenance", json_payload, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["revision_id"], ["project_runtime_revisions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("revision_id", "workspace_path", name="uq_materialization_revision_path"),
        sa.UniqueConstraint(
            "revision_id",
            "idempotency_key",
            name="uq_materialization_revision_idempotency",
        ),
        sa.CheckConstraint(
            "status IN ('planned', 'staged', 'applied', 'failed')",
            name="ck_materialization_entry_status",
        ),
        sa.CheckConstraint("generation >= 0", name="ck_materialization_entry_generation_nonnegative"),
    )
    op.create_index(
        "ix_materialization_entries_revision_status",
        "materialization_entries",
        ["revision_id", "status"],
    )
    op.create_index(
        "ix_materialization_entries_revision_generation",
        "materialization_entries",
        ["revision_id", "generation"],
    )

    op.create_table(
        "runtime_revision_heads",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.String(40), nullable=True),
        sa.Column("resource_key", sa.String(200), nullable=False),
        sa.Column("revision_id", sa.String(64), nullable=True),
        sa.Column("applied_revision_id", sa.String(64), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("phase", sa.String(24), nullable=False, server_default="idle"),
        sa.Column("fence_token", sa.String(128), nullable=True),
        sa.Column("applied_desired_digest", sa.String(128), nullable=True),
        sa.Column("ownership_digest", sa.String(128), nullable=True),
        sa.Column("uncertain_operations", json_payload, nullable=True),
        sa.Column("diagnostics", json_payload, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resource_id"], ["resources.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["revision_id"], ["project_runtime_revisions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["applied_revision_id"],
            ["project_runtime_revisions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resource_key", name="uq_runtime_revision_head_resource_key"),
        sa.UniqueConstraint("fence_token", name="uq_runtime_revision_head_fence_token"),
        sa.CheckConstraint("generation >= 0", name="ck_runtime_revision_head_generation_nonnegative"),
        sa.CheckConstraint(
            "phase IN ('idle', 'applying', 'applied', 'failed', 'rolled_back')",
            name="ck_runtime_revision_head_phase",
        ),
        sa.CheckConstraint("generation > 0 OR phase = 'idle'", name="ck_runtime_revision_head_idle_generation"),
        sa.CheckConstraint(
            "resource_key <> '' AND resource_key = btrim(resource_key) AND resource_key !~ '[[:space:]]'",
            name="ck_runtime_revision_head_resource_key_well_formed",
        ),
        sa.CheckConstraint(
            "applied_desired_digest IS NULL OR applied_desired_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_runtime_revision_head_desired_digest",
        ),
        sa.CheckConstraint(
            "ownership_digest IS NULL OR ownership_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_runtime_revision_head_ownership_digest",
        ),
        comment=(
            "Resource-wide runtime CAS head. Generation is allocated only by a conditional UPDATE "
            "on this row and is never decremented or reused, so empty plans are fenced too."
        ),
    )
    op.create_index(
        "ix_runtime_revision_heads_project_phase",
        "runtime_revision_heads",
        ["project_id", "phase"],
    )
    op.create_index("ix_runtime_revision_heads_resource_id", "runtime_revision_heads", ["resource_id"])

    op.create_table(
        "runtime_materialization_attempts",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.String(40), nullable=True),
        sa.Column("resource_key", sa.String(200), nullable=False),
        sa.Column("revision_id", sa.String(64), nullable=True),
        sa.Column("applied_revision_id", sa.String(64), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("plan_digest", sa.String(128), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("observed_generation", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(24), nullable=False, server_default="applying"),
        sa.Column("outcome", sa.String(24), nullable=True),
        sa.Column("receipt_digest", sa.String(128), nullable=True),
        sa.Column("fence_token", sa.String(128), nullable=False),
        sa.Column("rollback_of_generation", sa.Integer(), nullable=True),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("diagnostics", json_payload, nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resource_id"], ["resources.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["revision_id"], ["project_runtime_revisions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["applied_revision_id"],
            ["project_runtime_revisions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "resource_key",
            "idempotency_key",
            name="uq_runtime_attempt_resource_idempotency",
        ),
        sa.UniqueConstraint("resource_key", "generation", name="uq_runtime_attempt_resource_generation"),
        sa.CheckConstraint("generation >= 1", name="ck_runtime_attempt_generation_positive"),
        sa.CheckConstraint("observed_generation >= 0", name="ck_runtime_attempt_observed_generation_nonnegative"),
        sa.CheckConstraint(
            "generation = observed_generation + 1",
            name="ck_runtime_attempt_single_generation_increment",
        ),
        sa.CheckConstraint(
            "phase IN ('applying', 'applied', 'failed', 'rolled_back')",
            name="ck_runtime_attempt_phase",
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('applied', 'rolled_back', 'failed')",
            name="ck_runtime_attempt_outcome",
        ),
        sa.CheckConstraint("(phase = 'applying') = (outcome IS NULL)", name="ck_runtime_attempt_open_outcome"),
        sa.CheckConstraint(
            "phase <> 'applying' OR applied_revision_id IS NULL",
            name="ck_runtime_attempt_open_without_applied",
        ),
        sa.CheckConstraint(
            "(outcome IN ('applied', 'rolled_back')) = (applied_revision_id IS NOT NULL)",
            name="ck_runtime_attempt_applied_revision_link",
        ),
        sa.CheckConstraint(
            "(phase = 'applying') = (receipt_digest IS NULL)",
            name="ck_runtime_attempt_receipt_state",
        ),
        sa.CheckConstraint(
            "receipt_digest IS NULL OR receipt_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_runtime_attempt_receipt_digest",
        ),
        sa.CheckConstraint("plan_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_runtime_attempt_plan_digest"),
        sa.CheckConstraint(
            "rollback_of_generation IS NULL OR (rollback_of_generation >= 1 "
            "AND rollback_of_generation <= observed_generation)",
            name="ck_runtime_attempt_rollback_target",
        ),
        sa.CheckConstraint(
            "resource_key <> '' AND resource_key = btrim(resource_key) AND resource_key !~ '[[:space:]]'",
            name="ck_runtime_attempt_resource_key_well_formed",
        ),
        sa.CheckConstraint(
            "idempotency_key <> '' AND idempotency_key = btrim(idempotency_key)",
            name="ck_runtime_attempt_idempotency_key_well_formed",
        ),
        comment=(
            "Durable per-resource materialization attempt history: idempotency key, observed "
            "generation, one-generation increment, outcome and receipt digest."
        ),
    )
    op.create_index(
        "ix_runtime_materialization_attempts_resource_generation",
        "runtime_materialization_attempts",
        ["resource_key", sa.text("generation DESC")],
    )
    op.create_index(
        "ix_runtime_materialization_attempts_revision_generation",
        "runtime_materialization_attempts",
        ["revision_id", "generation"],
    )
    op.create_index(
        "ix_runtime_materialization_attempts_project_outcome",
        "runtime_materialization_attempts",
        ["project_id", "outcome"],
    )
    op.create_index(
        "ix_runtime_materialization_attempts_resource_id",
        "runtime_materialization_attempts",
        ["resource_id"],
    )

    op.create_table(
        "runtime_materialization_audit",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.String(40), nullable=True),
        sa.Column("resource_key", sa.String(200), nullable=False),
        sa.Column("attempt_id", sa.String(64), nullable=True),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("resource_digest", sa.String(128), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("correlation_digest", sa.String(128), nullable=False),
        sa.Column("plan_digest", sa.String(128), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["resource_id"], ["resources.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["runtime_materialization_attempts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("event ~ '^[a-z][a-z0-9_.-]{0,63}$'", name="ck_runtime_audit_event_code"),
        sa.CheckConstraint("outcome ~ '^[a-z][a-z0-9_.-]{0,63}$'", name="ck_runtime_audit_outcome_code"),
        sa.CheckConstraint(
            "reason_code ~ '^[a-z][a-z0-9_.-]{0,63}$'",
            name="ck_runtime_audit_reason_code_redacted",
        ),
        sa.CheckConstraint("generation >= 0", name="ck_runtime_audit_generation_nonnegative"),
        sa.CheckConstraint("resource_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_runtime_audit_resource_digest"),
        sa.CheckConstraint(
            "correlation_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_runtime_audit_correlation_digest",
        ),
        sa.CheckConstraint("plan_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_runtime_audit_plan_digest"),
        sa.CheckConstraint(
            "correlation_id <> '' AND correlation_id = btrim(correlation_id)",
            name="ck_runtime_audit_correlation_id_well_formed",
        ),
        comment=(
            "Append-only redacted materialization audit (reason code + correlation id). "
            "UPDATE/DELETE/TRUNCATE are rejected by trigger; INSERT-only grants belong to "
            "the RLS/privileges revision."
        ),
    )
    op.create_index(
        "ix_runtime_materialization_audit_resource_recorded",
        "runtime_materialization_audit",
        ["resource_key", "recorded_at"],
    )
    op.create_index(
        "ix_runtime_materialization_audit_project_recorded",
        "runtime_materialization_audit",
        ["project_id", "recorded_at"],
    )
    op.create_index(
        "ix_runtime_materialization_audit_attempt_id",
        "runtime_materialization_audit",
        ["attempt_id"],
    )

    # Append-only audit: the ORM cannot declare triggers, so the migration owns the
    # invariant. UPDATE/DELETE on a row and TRUNCATE of the table both fail closed.
    op.execute(
        f"""
        CREATE FUNCTION {_AUDIT_APPEND_ONLY_FUNCTION}() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'runtime_materialization_audit is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER trg_runtime_materialization_audit_append_only "
        "BEFORE UPDATE OR DELETE ON runtime_materialization_audit "
        f"FOR EACH ROW EXECUTE FUNCTION {_AUDIT_APPEND_ONLY_FUNCTION}()"
    )
    op.execute(
        "CREATE TRIGGER trg_runtime_materialization_audit_no_truncate "
        "BEFORE TRUNCATE ON runtime_materialization_audit "
        f"FOR EACH STATEMENT EXECUTE FUNCTION {_AUDIT_APPEND_ONLY_FUNCTION}()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_runtime_materialization_audit_no_truncate ON runtime_materialization_audit")
    op.execute("DROP TRIGGER IF EXISTS trg_runtime_materialization_audit_append_only ON runtime_materialization_audit")

    op.drop_index("ix_runtime_materialization_audit_attempt_id", table_name="runtime_materialization_audit")
    op.drop_index(
        "ix_runtime_materialization_audit_project_recorded",
        table_name="runtime_materialization_audit",
    )
    op.drop_index(
        "ix_runtime_materialization_audit_resource_recorded",
        table_name="runtime_materialization_audit",
    )
    op.drop_table("runtime_materialization_audit")
    op.execute(f"DROP FUNCTION IF EXISTS {_AUDIT_APPEND_ONLY_FUNCTION}()")

    op.drop_index(
        "ix_runtime_materialization_attempts_resource_id",
        table_name="runtime_materialization_attempts",
    )
    op.drop_index(
        "ix_runtime_materialization_attempts_project_outcome",
        table_name="runtime_materialization_attempts",
    )
    op.drop_index(
        "ix_runtime_materialization_attempts_revision_generation",
        table_name="runtime_materialization_attempts",
    )
    op.drop_index(
        "ix_runtime_materialization_attempts_resource_generation",
        table_name="runtime_materialization_attempts",
    )
    op.drop_table("runtime_materialization_attempts")

    op.drop_index("ix_runtime_revision_heads_resource_id", table_name="runtime_revision_heads")
    op.drop_index("ix_runtime_revision_heads_project_phase", table_name="runtime_revision_heads")
    op.drop_table("runtime_revision_heads")

    op.drop_index(
        "ix_materialization_entries_revision_generation",
        table_name="materialization_entries",
    )
    op.drop_index("ix_materialization_entries_revision_status", table_name="materialization_entries")
    op.drop_table("materialization_entries")

    op.drop_index(
        "ix_project_runtime_revisions_project_state_revision",
        table_name="project_runtime_revisions",
    )
    op.drop_table("project_runtime_revisions")

    op.drop_table("capability_bundles")

    op.drop_index("uq_resource_binding_target_without_session", table_name="resource_bindings")
    op.drop_index("ix_resource_bindings_target", table_name="resource_bindings")
    op.drop_table("resource_bindings")

    op.drop_index("ix_resource_versions_resource_status", table_name="resource_versions")
    op.drop_table("resource_versions")

    op.drop_index("ix_resources_scope", table_name="resources")
    op.drop_table("resources")
