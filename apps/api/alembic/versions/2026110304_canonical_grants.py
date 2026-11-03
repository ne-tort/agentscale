"""M06 canonical capability grant ownership: grants, mapping, quarantine, audit, fence.

Revision ``2026110304`` materialises the tables whose single owning description
is ``prodavan.infrastructure.persistence.models.canonical_grants``.  The
ownership decision is recorded in
``docs/08-migration/canonical-grants-ownership-decision.md``:

* ``dynamic_app_capability_grants``            canonical authorization rows;
* ``dynamic_app_capability_grant_mappings``    source -> canonical mapping;
* ``dynamic_app_capability_grant_quarantine``  visible, retryable rejects;
* ``dynamic_app_capability_audit``             append-shaped decision history;
* ``dynamic_app_capability_writer_epochs``     bounded-partition fencing epoch;
* ``m06_canonical_grant_promotion_evidence``   immutable review evidence.

Deliberate exclusions in this revision:

* no RLS/FORCE policies and no role privileges -- revision ``2026110307`` owns
  them, and ``canonical_grants.RLS_POLICY_DDL`` / ``M06_PRIVILEGE_INVENTORY``
  remain review metadata only;
* no backfill, no data import, no trigger and no positive writer enablement:
  the tables are created empty and every evidence row defaults to a closed
  outcome;
* the legacy ``dynamic_app_grants`` JSONB projection
  (``models/dynamic_apps.py::AppGrantRow``) is a different physical table and is
  intentionally not touched here.

Required parent keys from the earlier revisions of this linear chain:
``scope_registry`` PK + ``uq_scope_registry_kind_id`` (2026110301),
``m04e_dynamic_app_definitions`` ``uq_m04e_definition_id_tenant`` and
``m04e_dynamic_app_versions`` ``uq_m04e_version_id_definition_artifact``
(2026110302), ``m05_plugin_instances`` ``uq_m05_plugin_instance_grant_identity``
(2026110303).

Written under the migration verification freeze: this file is authored, not
executed.  No test, build, lint, compile, ``alembic upgrade`` or DDL run was
performed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "2026110304"
down_revision = "2026110303"
branch_labels = None
depends_on = None


_SCOPE_KINDS = "('tenant', 'cabinet', 'project', 'plugin_instance')"
_SUBJECT_KINDS = "('user', 'service', 'role')"
_EVIDENCE_STATES = "('open', 'resolved', 'archived')"
_PROMOTION_STATES = "('blocked', 'quarantined', 'ready', 'promotable')"

_SHA256_PATTERN = "'^sha256:[0-9a-f]{64}$'"


def upgrade() -> None:
    json_payload = postgresql.JSONB(astext_type=sa.Text())

    op.create_table(
        "dynamic_app_capability_grants",
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("app_definition_id", sa.String(80), nullable=False),
        sa.Column("app_id", sa.String(160), nullable=False),
        sa.Column("app_version_id", sa.String(80), nullable=False),
        sa.Column("app_artifact_digest", sa.String(71), nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("cabinet_id", sa.String(40), nullable=True),
        sa.Column("project_id", sa.String(40), nullable=True),
        sa.Column("scope_registry_id", sa.String(40), nullable=False),
        sa.Column("scope_kind", sa.String(32), nullable=False),
        sa.Column("scope_id", sa.String(40), nullable=False),
        sa.Column("capability_key", sa.String(200), nullable=False),
        sa.Column("subject_kind", sa.String(16), nullable=False),
        sa.Column("subject_id", sa.String(160), nullable=False),
        sa.Column("plugin_instance_id", sa.String(40), nullable=True),
        sa.Column("session_id", sa.String(160), nullable=True),
        sa.Column("operations", json_payload, nullable=False),
        sa.Column("resources", json_payload, nullable=False),
        sa.Column("domains", json_payload, nullable=False),
        sa.Column(
            "constraints",
            json_payload,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("operations_digest", sa.String(71), nullable=False),
        sa.Column("resources_digest", sa.String(71), nullable=False),
        sa.Column("domains_digest", sa.String(71), nullable=False),
        sa.Column("constraints_digest", sa.String(71), nullable=False),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_by", sa.String(160), nullable=False),
        sa.Column("revoked_by", sa.String(160), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.Column("grant_generation", sa.BigInteger(), nullable=False),
        sa.Column("writer_generation", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column(
            "source_kind",
            sa.String(64),
            nullable=False,
            server_default="canonical",
        ),
        sa.Column("source_id", sa.String(160), nullable=True),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("payload_digest", sa.String(71), nullable=False),
        sa.Column(
            "audit_metadata",
            json_payload,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        # Same-definition proof plus immutable artifact equality.  Column order
        # matches uq_m04e_version_id_definition_artifact on the version table.
        sa.ForeignKeyConstraint(
            ["app_version_id", "app_definition_id", "app_artifact_digest"],
            [
                "m04e_dynamic_app_versions.id",
                "m04e_dynamic_app_versions.definition_id",
                "m04e_dynamic_app_versions.artifact_digest",
            ],
            name="fk_cap_grant_version_definition_artifact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["app_definition_id"],
            ["m04e_dynamic_app_definitions.id"],
            name="fk_cap_grant_definition",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["app_definition_id", "tenant_id"],
            ["m04e_dynamic_app_definitions.id", "m04e_dynamic_app_definitions.tenant_id"],
            name="fk_cap_grant_definition_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scope_registry_id"],
            ["scope_registry.id"],
            name="fk_cap_grant_scope_registry",
            ondelete="RESTRICT",
        ),
        # Exact runtime binding: tenant, definition, version and typed scope are
        # proven by the instance row itself, not by adapter assertions.  MATCH
        # SIMPLE keeps instance-independent (policy-approved) rows admissible;
        # every row that does name an instance is fully constrained.
        sa.ForeignKeyConstraint(
            [
                "plugin_instance_id",
                "app_definition_id",
                "app_version_id",
                "tenant_id",
                "scope_kind",
                "scope_id",
            ],
            [
                "m05_plugin_instances.id",
                "m05_plugin_instances.definition_id",
                "m05_plugin_instances.version_id",
                "m05_plugin_instances.tenant_id",
                "m05_plugin_instances.scope_kind",
                "m05_plugin_instances.scope_id",
            ],
            name="fk_cap_grant_instance_exact",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(f"scope_kind IN {_SCOPE_KINDS}", name="ck_cap_grant_scope_kind"),
        sa.CheckConstraint(
            "(scope_kind = 'tenant' AND scope_id = tenant_id AND cabinet_id IS NULL "
            "AND project_id IS NULL AND plugin_instance_id IS NULL) "
            "OR (scope_kind = 'cabinet' AND scope_id = cabinet_id AND cabinet_id IS NOT NULL "
            "AND project_id IS NULL AND plugin_instance_id IS NULL) "
            "OR (scope_kind = 'project' AND scope_id = project_id AND cabinet_id IS NOT NULL "
            "AND project_id IS NOT NULL AND plugin_instance_id IS NULL) "
            "OR (scope_kind = 'plugin_instance' AND scope_id = plugin_instance_id "
            "AND plugin_instance_id IS NOT NULL)",
            name="ck_cap_grant_scope_shape",
        ),
        sa.CheckConstraint(f"subject_kind IN {_SUBJECT_KINDS}", name="ck_cap_grant_subject_kind"),
        sa.CheckConstraint(
            "length(btrim(capability_key)) > 0",
            name="ck_cap_grant_capability_nonempty",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(operations) = 'array' AND jsonb_array_length(operations) > 0",
            name="ck_cap_grant_operations_nonempty",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(resources) = 'array' AND jsonb_array_length(resources) > 0",
            name="ck_cap_grant_resources_nonempty",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(domains) = 'array' AND jsonb_array_length(domains) > 0",
            name="ck_cap_grant_domains_nonempty",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(constraints) = 'object'",
            name="ck_cap_grant_constraints_object",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > issued_at",
            name="ck_cap_grant_expiry",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR (revoked_at >= issued_at AND revoked_by IS NOT NULL "
            "AND length(btrim(revoke_reason)) > 0)",
            name="ck_cap_grant_revoke",
        ),
        sa.CheckConstraint(
            "grant_generation > 0 AND writer_generation > 0",
            name="ck_cap_grant_generation",
        ),
        sa.CheckConstraint(
            "plugin_instance_id IS NOT NULL OR session_id IS NULL",
            name="ck_cap_grant_plugin_binding",
        ),
        sa.CheckConstraint(
            "tenant_id <> '' AND app_definition_id <> '' AND app_version_id <> ''",
            name="ck_cap_grant_identity_nonempty",
        ),
        sa.CheckConstraint(
            f"app_artifact_digest ~ {_SHA256_PATTERN}",
            name="ck_cap_grant_artifact_sha256",
        ),
        sa.CheckConstraint(
            f"operations_digest ~ {_SHA256_PATTERN}",
            name="ck_cap_grant_operations_sha256",
        ),
        sa.CheckConstraint(
            f"resources_digest ~ {_SHA256_PATTERN}",
            name="ck_cap_grant_resources_sha256",
        ),
        sa.CheckConstraint(
            f"domains_digest ~ {_SHA256_PATTERN}",
            name="ck_cap_grant_domains_sha256",
        ),
        sa.CheckConstraint(
            f"constraints_digest ~ {_SHA256_PATTERN}",
            name="ck_cap_grant_constraints_sha256",
        ),
        sa.CheckConstraint(
            f"payload_digest ~ {_SHA256_PATTERN}",
            name="ck_cap_grant_payload_sha256",
        ),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_cap_grant_idempotency"),
    )
    # Active identity is the full authorization tuple.  Expiry is deliberately
    # absent from the predicate: a moving clock must never appear in an index,
    # and expired rows stay unique evidence until an explicit revoke/supersede.
    op.create_index(
        "uq_cap_grant_active_identity",
        "dynamic_app_capability_grants",
        [
            "tenant_id",
            "app_version_id",
            "capability_key",
            "subject_kind",
            "subject_id",
            sa.text("COALESCE(plugin_instance_id, '')"),
            "scope_kind",
            "scope_id",
            "operations_digest",
            "resources_digest",
            "domains_digest",
            "constraints_digest",
        ],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "ix_cap_grant_authorize",
        "dynamic_app_capability_grants",
        [
            "tenant_id",
            "scope_kind",
            "scope_id",
            "app_definition_id",
            "app_version_id",
            "capability_key",
            "subject_kind",
            "subject_id",
            "plugin_instance_id",
        ],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "ix_cap_grant_validity",
        "dynamic_app_capability_grants",
        ["tenant_id", "issued_at", "expires_at", "revoked_at"],
    )
    op.create_index(
        "ix_cap_grant_instance_generation",
        "dynamic_app_capability_grants",
        ["plugin_instance_id", "writer_generation", "grant_generation"],
        postgresql_where=sa.text("plugin_instance_id IS NOT NULL"),
    )
    op.create_index(
        "ix_cap_grant_source",
        "dynamic_app_capability_grants",
        ["source_kind", "source_id", "schema_version"],
    )

    op.create_table(
        "dynamic_app_capability_grant_mappings",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("source_kind", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(160), nullable=False),
        sa.Column("canonical_schema_version", sa.String(32), nullable=False),
        sa.Column("source_payload_digest", sa.String(71), nullable=False),
        sa.Column("canonical_payload_digest", sa.String(71), nullable=True),
        sa.Column("target_grant_id", sa.String(80), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="quarantined"),
        sa.Column("reason_code", sa.String(80), nullable=True),
        sa.Column("checkpoint", sa.String(160), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_kind",
            "source_id",
            "canonical_schema_version",
            name="uq_cap_grant_mapping_source",
        ),
        sa.ForeignKeyConstraint(
            ["target_grant_id"],
            ["dynamic_app_capability_grants.id"],
            name="fk_cap_grant_mapping_target",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('mapped', 'quarantined', 'archived')",
            name="ck_cap_grant_mapping_status",
        ),
        sa.CheckConstraint(
            f"source_payload_digest ~ {_SHA256_PATTERN}",
            name="ck_cap_grant_mapping_source_sha256",
        ),
    )
    op.create_index(
        "ix_cap_grant_mapping_checkpoint",
        "dynamic_app_capability_grant_mappings",
        ["checkpoint"],
    )

    op.create_table(
        "dynamic_app_capability_grant_quarantine",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("source_kind", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(160), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(80), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("source_payload_digest", sa.String(71), nullable=False),
        sa.Column("resolved_tenant_id", sa.String(40), nullable=True),
        sa.Column("resolved_app_definition_id", sa.String(80), nullable=True),
        sa.Column("resolved_app_version_id", sa.String(80), nullable=True),
        sa.Column("resolved_scope_kind", sa.String(32), nullable=True),
        sa.Column("resolved_scope_id", sa.String(80), nullable=True),
        sa.Column("resolved_subject_kind", sa.String(16), nullable=True),
        sa.Column("resolved_subject_id", sa.String(160), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("checkpoint", sa.String(160), nullable=True),
        sa.Column(
            "redacted_metadata",
            json_payload,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_kind",
            "source_id",
            "schema_version",
            "source_payload_digest",
            name="uq_cap_grant_quarantine_observation",
        ),
        sa.CheckConstraint(
            f"status IN {_EVIDENCE_STATES}",
            name="ck_cap_grant_quarantine_status",
        ),
        sa.CheckConstraint(
            "length(btrim(reason_code)) > 0",
            name="ck_cap_grant_quarantine_reason",
        ),
        sa.CheckConstraint(
            f"source_payload_digest ~ {_SHA256_PATTERN}",
            name="ck_cap_grant_quarantine_source_sha256",
        ),
    )
    op.create_index(
        "ix_cap_grant_quarantine_open",
        "dynamic_app_capability_grant_quarantine",
        ["status", "source_kind", "last_seen_at"],
    )
    op.create_index(
        "ix_cap_grant_quarantine_tenant",
        "dynamic_app_capability_grant_quarantine",
        ["resolved_tenant_id", "status"],
    )

    op.create_table(
        "dynamic_app_capability_audit",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("event_id", sa.String(160), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(80), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("tenant_id", sa.String(40), nullable=True),
        sa.Column("cabinet_id", sa.String(40), nullable=True),
        sa.Column("project_id", sa.String(40), nullable=True),
        sa.Column("session_id", sa.String(160), nullable=True),
        sa.Column("subject_kind", sa.String(16), nullable=True),
        sa.Column("subject_id", sa.String(160), nullable=True),
        sa.Column("app_definition_id", sa.String(80), nullable=True),
        sa.Column("app_version_id", sa.String(80), nullable=True),
        sa.Column("capability_grant_id", sa.String(80), nullable=True),
        sa.Column("plugin_instance_id", sa.String(40), nullable=True),
        sa.Column("grant_generation", sa.BigInteger(), nullable=True),
        sa.Column("writer_generation", sa.BigInteger(), nullable=True),
        sa.Column("actor_id", sa.String(160), nullable=True),
        sa.Column("request_id", sa.String(160), nullable=True),
        sa.Column("trace_id", sa.String(160), nullable=True),
        sa.Column("idempotency_key", sa.String(200), nullable=True),
        sa.Column("capability_key", sa.String(200), nullable=True),
        sa.Column("operation", sa.String(160), nullable=True),
        sa.Column("resource", sa.String(512), nullable=True),
        sa.Column("domain", sa.String(512), nullable=True),
        sa.Column("payload_digest", sa.String(71), nullable=True),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("redaction", sa.String(32), nullable=False),
        sa.Column("mcp_span_id", sa.String(160), nullable=True),
        sa.Column("mcp_profile_id", sa.String(160), nullable=True),
        sa.Column("mcp_tool_server", sa.String(200), nullable=True),
        sa.Column("mcp_tool_name", sa.String(200), nullable=True),
        sa.Column("mcp_tool_version", sa.String(80), nullable=True),
        sa.Column("mcp_arguments_digest", sa.String(71), nullable=True),
        sa.Column("mcp_result_status", sa.String(32), nullable=True),
        sa.Column("mcp_error_code", sa.String(80), nullable=True),
        sa.Column("mcp_duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "metadata",
            json_payload,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_cap_audit_event"),
        sa.ForeignKeyConstraint(
            ["capability_grant_id"],
            ["dynamic_app_capability_grants.id"],
            name="fk_cap_audit_grant",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("length(btrim(event_type)) > 0", name="ck_cap_audit_event_type"),
        sa.CheckConstraint("length(btrim(decision)) > 0", name="ck_cap_audit_decision"),
        sa.CheckConstraint("length(btrim(reason_code)) > 0", name="ck_cap_audit_reason"),
    )
    op.create_index(
        "ix_cap_audit_tenant_time",
        "dynamic_app_capability_audit",
        ["tenant_id", "occurred_at"],
    )
    op.create_index(
        "ix_cap_audit_grant_time",
        "dynamic_app_capability_audit",
        ["capability_grant_id", "occurred_at"],
    )
    op.create_index(
        "ix_cap_audit_trace",
        "dynamic_app_capability_audit",
        ["trace_id", "occurred_at"],
    )

    op.create_table(
        "dynamic_app_capability_writer_epochs",
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("partition_id", sa.String(160), nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("app_definition_id", sa.String(80), nullable=False),
        sa.Column("scope_kind", sa.String(32), nullable=False),
        sa.Column("scope_id", sa.String(40), nullable=False),
        sa.Column("writer_epoch", sa.BigInteger(), nullable=False),
        sa.Column("advanced_by", sa.String(160), nullable=False),
        sa.Column("correlation_id", sa.String(160), nullable=False),
        sa.Column(
            "advanced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "app_definition_id",
            "scope_kind",
            "scope_id",
            name="uq_cap_grant_writer_epoch_partition",
        ),
        sa.UniqueConstraint(
            "partition_id",
            name="uq_cap_grant_writer_epoch_partition_id",
        ),
        sa.ForeignKeyConstraint(
            ["app_definition_id", "tenant_id"],
            ["m04e_dynamic_app_definitions.id", "m04e_dynamic_app_definitions.tenant_id"],
            name="fk_cap_grant_writer_epoch_definition_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scope_kind", "scope_id"],
            ["scope_registry.scope_kind", "scope_registry.scope_id"],
            name="fk_cap_grant_writer_epoch_scope",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "tenant_id <> '' AND app_definition_id <> '' AND scope_id <> ''",
            name="ck_cap_grant_writer_epoch_identity_nonempty",
        ),
        sa.CheckConstraint(
            f"scope_kind IN {_SCOPE_KINDS}",
            name="ck_cap_grant_writer_epoch_scope_kind",
        ),
        sa.CheckConstraint("writer_epoch > 0", name="ck_cap_grant_writer_epoch_positive"),
        sa.CheckConstraint(
            "partition_id = tenant_id || ':' || app_definition_id || ':' || scope_kind "
            "|| ':' || scope_id",
            name="ck_cap_grant_writer_epoch_partition_identity",
        ),
    )
    op.create_index(
        "ix_cap_grant_writer_epoch_lookup",
        "dynamic_app_capability_writer_epochs",
        ["tenant_id", "app_definition_id", "scope_kind", "scope_id"],
    )

    op.create_table(
        "m06_canonical_grant_promotion_evidence",
        sa.Column("id", sa.String(80), nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("definition_id", sa.String(80), nullable=False),
        sa.Column("partition_id", sa.String(160), nullable=False),
        sa.Column("revision_id", sa.String(80), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False, server_default="blocked"),
        sa.Column("source_snapshot_id", sa.String(200), nullable=False),
        sa.Column("source_schema_version", sa.String(32), nullable=False),
        sa.Column("source_digest", sa.String(71), nullable=False),
        sa.Column("target_digest", sa.String(71), nullable=False),
        sa.Column("dependency_digests", json_payload, nullable=False),
        sa.Column("constraint_inventory", json_payload, nullable=False),
        sa.Column("index_inventory", json_payload, nullable=False),
        sa.Column("policy_inventory", json_payload, nullable=False),
        sa.Column("trusted_role_epoch_evidence", json_payload, nullable=False),
        sa.Column("gate_evidence", json_payload, nullable=False),
        sa.Column(
            "canonical_positive_writes",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "writer_epoch",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("correlation_id", sa.String(160), nullable=False),
        sa.Column(
            "decision_owner",
            sa.String(200),
            nullable=False,
            server_default="legacy",
        ),
        sa.Column("reviewer", sa.String(200), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "rollback_gate",
            sa.Text(),
            nullable=False,
            server_default="legacy remains authoritative; preserve evidence and repair forward",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "definition_id",
            "partition_id",
            "revision_id",
            name="uq_m06_grant_promotion_revision",
        ),
        sa.ForeignKeyConstraint(
            ["definition_id", "tenant_id"],
            ["m04e_dynamic_app_definitions.id", "m04e_dynamic_app_definitions.tenant_id"],
            name="fk_m06_grant_promotion_definition_tenant",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"outcome IN {_PROMOTION_STATES}",
            name="ck_m06_grant_promotion_outcome",
        ),
        sa.CheckConstraint(
            "canonical_positive_writes = false",
            name="ck_m06_grant_no_positive_writes",
        ),
        sa.CheckConstraint(
            f"source_digest ~ {_SHA256_PATTERN}",
            name="ck_m06_grant_promotion_source_sha256",
        ),
        sa.CheckConstraint(
            f"target_digest ~ {_SHA256_PATTERN}",
            name="ck_m06_grant_promotion_target_sha256",
        ),
    )
    op.create_index(
        "ix_m06_grant_promotion_partition_time",
        "m06_canonical_grant_promotion_evidence",
        ["tenant_id", "definition_id", "partition_id", "recorded_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_m06_grant_promotion_partition_time",
        table_name="m06_canonical_grant_promotion_evidence",
    )
    op.drop_table("m06_canonical_grant_promotion_evidence")

    op.drop_index(
        "ix_cap_grant_writer_epoch_lookup",
        table_name="dynamic_app_capability_writer_epochs",
    )
    op.drop_table("dynamic_app_capability_writer_epochs")

    op.drop_index("ix_cap_audit_trace", table_name="dynamic_app_capability_audit")
    op.drop_index("ix_cap_audit_grant_time", table_name="dynamic_app_capability_audit")
    op.drop_index("ix_cap_audit_tenant_time", table_name="dynamic_app_capability_audit")
    op.drop_table("dynamic_app_capability_audit")

    op.drop_index(
        "ix_cap_grant_quarantine_tenant",
        table_name="dynamic_app_capability_grant_quarantine",
    )
    op.drop_index(
        "ix_cap_grant_quarantine_open",
        table_name="dynamic_app_capability_grant_quarantine",
    )
    op.drop_table("dynamic_app_capability_grant_quarantine")

    op.drop_index(
        "ix_cap_grant_mapping_checkpoint",
        table_name="dynamic_app_capability_grant_mappings",
    )
    op.drop_table("dynamic_app_capability_grant_mappings")

    op.drop_index(
        "ix_cap_grant_source",
        table_name="dynamic_app_capability_grants",
    )
    op.drop_index(
        "ix_cap_grant_instance_generation",
        table_name="dynamic_app_capability_grants",
    )
    op.drop_index("ix_cap_grant_validity", table_name="dynamic_app_capability_grants")
    op.drop_index("ix_cap_grant_authorize", table_name="dynamic_app_capability_grants")
    op.drop_index(
        "uq_cap_grant_active_identity",
        table_name="dynamic_app_capability_grants",
    )
    op.drop_table("dynamic_app_capability_grants")
