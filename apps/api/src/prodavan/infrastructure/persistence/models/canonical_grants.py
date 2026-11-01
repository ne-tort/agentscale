"""M06 canonical grant persistence target and review evidence.

This module is deliberately schema-only.  It defines the target persistence
surface for canonical grants, mapping, quarantine and append-shaped audit
history.  M04/M04.e/M05 gates are not satisfied in the migration workspace,
so no positive import or trusted writer is exposed here.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


_SCOPE_KINDS = "('tenant', 'cabinet', 'project', 'plugin_instance')"
_SUBJECT_KINDS = "('user', 'service', 'role')"
_EVIDENCE_STATES = "('open', 'resolved', 'archived')"
_PROMOTION_STATES = "('blocked', 'quarantined', 'ready', 'promotable')"


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


class CanonicalGrantRow(Base):
    """One capability identity bound to an exact definition and scope."""

    __tablename__ = "dynamic_app_capability_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["app_version_id", "app_definition_id"],
            ["m04e_dynamic_app_versions.id", "m04e_dynamic_app_versions.definition_id"],
            name="fk_cap_grant_version_definition",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scope_registry_id"],
            ["scope_registry.id"],
            name="fk_cap_grant_scope_registry",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["app_definition_id"],
            ["m04e_dynamic_app_definitions.id"],
            name="fk_cap_grant_definition",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["app_definition_id", "tenant_id"],
            ["m04e_dynamic_app_definitions.id", "m04e_dynamic_app_definitions.tenant_id"],
            name="fk_cap_grant_definition_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["plugin_instance_id", "app_definition_id", "app_version_id", "tenant_id", "scope_kind", "scope_id"],
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
        CheckConstraint(f"scope_kind IN {_SCOPE_KINDS}", name="ck_cap_grant_scope_kind"),
        CheckConstraint(
            "(scope_kind = 'tenant' AND scope_id = tenant_id AND cabinet_id IS NULL AND project_id IS NULL AND plugin_instance_id IS NULL) "
            "OR (scope_kind = 'cabinet' AND scope_id = cabinet_id AND cabinet_id IS NOT NULL AND project_id IS NULL AND plugin_instance_id IS NULL) "
            "OR (scope_kind = 'project' AND scope_id = project_id AND cabinet_id IS NOT NULL AND project_id IS NOT NULL AND plugin_instance_id IS NULL) "
            "OR (scope_kind = 'plugin_instance' AND scope_id = plugin_instance_id AND plugin_instance_id IS NOT NULL)",
            name="ck_cap_grant_scope_shape",
        ),
        CheckConstraint(f"subject_kind IN {_SUBJECT_KINDS}", name="ck_cap_grant_subject_kind"),
        CheckConstraint("length(btrim(capability_key)) > 0", name="ck_cap_grant_capability_nonempty"),
        CheckConstraint("jsonb_typeof(operations) = 'array' AND jsonb_array_length(operations) > 0", name="ck_cap_grant_operations_nonempty"),
        CheckConstraint("jsonb_typeof(resources) = 'array' AND jsonb_array_length(resources) > 0", name="ck_cap_grant_resources_nonempty"),
        CheckConstraint("jsonb_typeof(domains) = 'array' AND jsonb_array_length(domains) > 0", name="ck_cap_grant_domains_nonempty"),
        CheckConstraint("jsonb_typeof(constraints) = 'object'", name="ck_cap_grant_constraints_object"),
        CheckConstraint("expires_at IS NULL OR expires_at > issued_at", name="ck_cap_grant_expiry"),
        CheckConstraint("revoked_at IS NULL OR (revoked_at >= issued_at AND revoked_by IS NOT NULL AND length(btrim(revoke_reason)) > 0)", name="ck_cap_grant_revoke"),
        CheckConstraint("grant_generation > 0 AND writer_generation > 0", name="ck_cap_grant_generation"),
        CheckConstraint("plugin_instance_id IS NOT NULL OR session_id IS NULL", name="ck_cap_grant_plugin_binding"),
        CheckConstraint("tenant_id <> '' AND app_definition_id <> '' AND app_version_id <> ''", name="ck_cap_grant_identity_nonempty"),
        CheckConstraint("app_artifact_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_cap_grant_artifact_sha256"),
        CheckConstraint("operations_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_cap_grant_operations_sha256"),
        CheckConstraint("resources_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_cap_grant_resources_sha256"),
        CheckConstraint("domains_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_cap_grant_domains_sha256"),
        CheckConstraint("constraints_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_cap_grant_constraints_sha256"),
        CheckConstraint("payload_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_cap_grant_payload_sha256"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_cap_grant_idempotency"),
        Index(
            "uq_cap_grant_active_identity",
            "tenant_id", "app_version_id", "capability_key", "subject_kind", "subject_id",
            text("COALESCE(plugin_instance_id, '')"), "scope_kind", "scope_id",
            "operations_digest", "resources_digest", "domains_digest", "constraints_digest",
            unique=True, postgresql_where=text("revoked_at IS NULL"),
        ),
        Index(
            "ix_cap_grant_authorize", "tenant_id", "scope_kind", "scope_id",
            "app_definition_id", "app_version_id", "capability_key", "subject_kind",
            "subject_id", "plugin_instance_id", postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_cap_grant_validity", "tenant_id", "issued_at", "expires_at", "revoked_at"),
        Index("ix_cap_grant_instance_generation", "plugin_instance_id", "writer_generation", "grant_generation", postgresql_where=text("plugin_instance_id IS NOT NULL")),
        Index("ix_cap_grant_source", "source_kind", "source_id", "schema_version"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=lambda: _id("cgrant"))
    app_definition_id: Mapped[str] = mapped_column(String(80), nullable=False)
    app_id: Mapped[str] = mapped_column(String(160), nullable=False)
    app_version_id: Mapped[str] = mapped_column(String(80), nullable=False)
    app_artifact_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(40), nullable=False)
    cabinet_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    scope_registry_id: Mapped[str] = mapped_column(String(40), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(80), nullable=False)
    capability_key: Mapped[str] = mapped_column(String(200), nullable=False)
    subject_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(160), nullable=False)
    plugin_instance_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    operations: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    resources: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    domains: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    constraints: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    operations_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    resources_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    domains_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    constraints_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    issued_by: Mapped[str] = mapped_column(String(160), nullable=False)
    revoked_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    grant_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    writer_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False, server_default="canonical")
    source_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    audit_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CanonicalGrantMappingRow(Base):
    __tablename__ = "dynamic_app_capability_grant_mappings"
    __table_args__ = (
        UniqueConstraint("source_kind", "source_id", "canonical_schema_version", name="uq_cap_grant_mapping_source"),
        ForeignKeyConstraint(["target_grant_id"], ["dynamic_app_capability_grants.id"], name="fk_cap_grant_mapping_target", ondelete="SET NULL"),
        CheckConstraint("status IN ('mapped', 'quarantined', 'archived')", name="ck_cap_grant_mapping_status"),
        CheckConstraint("source_payload_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_cap_grant_mapping_source_sha256"),
        Index("ix_cap_grant_mapping_checkpoint", "checkpoint"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(160), nullable=False)
    canonical_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    source_payload_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    canonical_payload_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    target_grant_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="quarantined")
    reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    checkpoint: Mapped[str | None] = mapped_column(String(160), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CanonicalGrantQuarantineRow(Base):
    __tablename__ = "dynamic_app_capability_grant_quarantine"
    __table_args__ = (
        UniqueConstraint("source_kind", "source_id", "schema_version", "source_payload_digest", name="uq_cap_grant_quarantine_observation"),
        CheckConstraint(f"status IN {_EVIDENCE_STATES}", name="ck_cap_grant_quarantine_status"),
        CheckConstraint("length(btrim(reason_code)) > 0", name="ck_cap_grant_quarantine_reason"),
        CheckConstraint("source_payload_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_cap_grant_quarantine_source_sha256"),
        Index("ix_cap_grant_quarantine_open", "status", "source_kind", "last_seen_at"),
        Index("ix_cap_grant_quarantine_tenant", "resolved_tenant_id", "status"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(160), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="open")
    source_payload_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    resolved_tenant_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resolved_app_definition_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    resolved_app_version_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    resolved_scope_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolved_scope_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    resolved_subject_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    resolved_subject_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    checkpoint: Mapped[str | None] = mapped_column(String(160), nullable=True)
    redacted_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")


class CanonicalGrantAuditRow(Base):
    __tablename__ = "dynamic_app_capability_audit"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_cap_audit_event"),
        ForeignKeyConstraint(
            ["capability_grant_id"],
            ["dynamic_app_capability_grants.id"],
            name="fk_cap_audit_grant",
            ondelete="SET NULL",
        ),
        CheckConstraint("length(btrim(event_type)) > 0", name="ck_cap_audit_event_type"),
        CheckConstraint("length(btrim(decision)) > 0", name="ck_cap_audit_decision"),
        CheckConstraint("length(btrim(reason_code)) > 0", name="ck_cap_audit_reason"),
        Index("ix_cap_audit_tenant_time", "tenant_id", "occurred_at"),
        Index("ix_cap_audit_grant_time", "capability_grant_id", "occurred_at"),
        Index("ix_cap_audit_trace", "trace_id", "occurred_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(80), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    tenant_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cabinet_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    subject_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    subject_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    app_definition_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    app_version_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    capability_grant_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    plugin_instance_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    grant_generation: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    writer_generation: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    capability_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    operation: Mapped[str | None] = mapped_column(String(160), nullable=True)
    resource: Mapped[str | None] = mapped_column(String(512), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(512), nullable=True)
    payload_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    redaction: Mapped[str] = mapped_column(String(32), nullable=False)
    mcp_span_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    mcp_profile_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    mcp_tool_server: Mapped[str | None] = mapped_column(String(200), nullable=True)
    mcp_tool_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    mcp_tool_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    mcp_arguments_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    mcp_result_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mcp_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    mcp_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audit_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, server_default="{}")


class CanonicalGrantWriterEpochRow(Base):
    """Trusted writer fence state, one current epoch per bounded partition."""

    __tablename__ = "dynamic_app_capability_writer_epochs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "app_definition_id", "scope_kind", "scope_id", name="uq_cap_grant_writer_epoch_partition"),
        UniqueConstraint("partition_id", name="uq_cap_grant_writer_epoch_partition_id"),
        ForeignKeyConstraint(
            ["app_definition_id", "tenant_id"],
            ["m04e_dynamic_app_definitions.id", "m04e_dynamic_app_definitions.tenant_id"],
            name="fk_cap_grant_writer_epoch_definition_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scope_kind", "scope_id"],
            ["scope_registry.scope_kind", "scope_registry.scope_id"],
            name="fk_cap_grant_writer_epoch_scope",
            ondelete="RESTRICT",
        ),
        CheckConstraint("tenant_id <> '' AND app_definition_id <> '' AND scope_id <> ''", name="ck_cap_grant_writer_epoch_identity_nonempty"),
        CheckConstraint(f"scope_kind IN {_SCOPE_KINDS}", name="ck_cap_grant_writer_epoch_scope_kind"),
        CheckConstraint("writer_epoch > 0", name="ck_cap_grant_writer_epoch_positive"),
        CheckConstraint("partition_id = tenant_id || ':' || app_definition_id || ':' || scope_kind || ':' || scope_id", name="ck_cap_grant_writer_epoch_partition_identity"),
        Index("ix_cap_grant_writer_epoch_lookup", "tenant_id", "app_definition_id", "scope_kind", "scope_id"),
    )
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=lambda: _id("wepoch"))
    partition_id: Mapped[str] = mapped_column(String(160), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(40), nullable=False)
    app_definition_id: Mapped[str] = mapped_column(String(80), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(80), nullable=False)
    writer_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    advanced_by: Mapped[str] = mapped_column(String(160), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(160), nullable=False)
    advanced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class M06CanonicalGrantPromotionEvidenceRow(Base):
    """Immutable review record; the database check permanently denies positive imports."""

    __tablename__ = "m06_canonical_grant_promotion_evidence"
    __table_args__ = (
        UniqueConstraint("tenant_id", "definition_id", "partition_id", "revision_id", name="uq_m06_grant_promotion_revision"),
        CheckConstraint(f"outcome IN {_PROMOTION_STATES}", name="ck_m06_grant_promotion_outcome"),
        CheckConstraint("canonical_positive_writes = false", name="ck_m06_grant_no_positive_writes"),
        CheckConstraint("source_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_m06_grant_promotion_source_sha256"),
        CheckConstraint("target_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_m06_grant_promotion_target_sha256"),
        Index("ix_m06_grant_promotion_partition_time", "tenant_id", "definition_id", "partition_id", "recorded_at"),
    )
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=lambda: _id("m06promo"))
    tenant_id: Mapped[str] = mapped_column(String(40), nullable=False)
    definition_id: Mapped[str] = mapped_column(String(80), nullable=False)
    partition_id: Mapped[str] = mapped_column(String(160), nullable=False)
    revision_id: Mapped[str] = mapped_column(String(80), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, server_default="blocked")
    source_snapshot_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    target_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    dependency_digests: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    constraint_inventory: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    index_inventory: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    policy_inventory: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    trusted_role_epoch_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    gate_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    canonical_positive_writes: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    writer_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    correlation_id: Mapped[str] = mapped_column(String(160), nullable=False)
    decision_owner: Mapped[str] = mapped_column(String(200), nullable=False, server_default="legacy")
    reviewer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    rollback_gate: Mapped[str] = mapped_column(Text, nullable=False, server_default="legacy remains authoritative; preserve evidence and repair forward")


# Static review inputs; they are intentionally not executable tests.
M06_ACCEPTANCE_CASES = (
    "same_definition_composite_version_fk_restricts_cross_definition_link",
    "exact_instance_composite_fk_restricts_cross_tenant_or_scope_link",
    "all_four_scope_shapes_include_plugin_instance",
    "active_identity_excludes_moving_clock_expiry_predicate",
    "nonempty_canonical_arrays_and_constraint_object_are_checked",
    "mapping_digest_conflict_is_quarantined_not_overwritten",
    "audit_update_delete_is_denied_for_api_roles",
    "rls_no_context_and_cross_tenant_using_with_check_deny",
    "trusted_writer_epoch_mismatch_denies_mutation",
    "promotion_record_keeps_canonical_positive_writes_false",
)

M06_DEPENDENCY_BLOCKERS = (
    "MIG-M04-02 incomplete: typed owner/scope registry and tenant anchors are not promoted",
    "MIG-M04E-02 incomplete: definition/version foundation validation and artifact proof are not promoted",
    "MIG-M05-01 incomplete: exact instance reciprocal key/runtime fencing evidence is not promoted",
    "approved capability identity and method-to-operation mapping evidence is absent",
)

M06_ROLLBACK_GATE = {
    "before_import": "remove only empty target objects after dependency review",
    "after_evidence_import": "stop partition writes, preserve grant/mapping/quarantine/audit evidence, repair forward",
    "canonical_read_owner": "legacy",
    "positive_write_gate": "blocked until all upstream gates, RLS/FORCE policy review, audit and writer epoch evidence pass",
}


# PostgreSQL policy inventory is attached as DDL metadata for the later Alembic
# revision.  It records exact policy shape without enabling a positive writer in
# application code; every policy denies absent context and stale writer epochs.
RLS_POLICY_DDL = {
    "grant": (
        "ALTER TABLE dynamic_app_capability_grants ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE dynamic_app_capability_grants FORCE ROW LEVEL SECURITY",
        "CREATE POLICY m06_cap_grant_tenant_access ON dynamic_app_capability_grants FOR ALL "
        "USING (current_setting('app.tenant_id', true) IS NOT NULL AND tenant_id = current_setting('app.tenant_id', true) "
        "AND current_setting('app.tenant_id', true) <> '' AND scope_registry_id IS NOT NULL "
        "AND EXISTS (SELECT 1 FROM m04e_dynamic_app_definitions d WHERE d.id = app_definition_id "
        "AND d.tenant_id = tenant_id) "
        "AND EXISTS (SELECT 1 FROM m04e_dynamic_app_versions v WHERE v.id = app_version_id "
        "AND v.definition_id = app_definition_id) "
        "AND scope_registry_id IN (SELECT sr.id FROM scope_registry sr WHERE sr.owner_state = 'active' "
        "AND sr.tenant_id = current_setting('app.tenant_id', true))) "
        "WITH CHECK (current_setting('app.tenant_id', true) IS NOT NULL AND tenant_id = current_setting('app.tenant_id', true) "
        "AND current_setting('app.tenant_id', true) <> '' AND scope_registry_id IS NOT NULL "
        "AND EXISTS (SELECT 1 FROM m04e_dynamic_app_definitions d WHERE d.id = app_definition_id "
        "AND d.tenant_id = tenant_id) "
        "AND EXISTS (SELECT 1 FROM m04e_dynamic_app_versions v WHERE v.id = app_version_id "
        "AND v.definition_id = app_definition_id) "
        "AND EXISTS (SELECT 1 FROM scope_registry sr WHERE sr.id = scope_registry_id "
        "AND sr.owner_state = 'active' AND sr.tenant_id = tenant_id "
        "AND sr.scope_kind = scope_kind AND sr.scope_id = scope_id "
        "AND sr.cabinet_id IS NOT DISTINCT FROM cabinet_id "
        "AND sr.project_id IS NOT DISTINCT FROM project_id "
        "AND sr.plugin_instance_id IS NOT DISTINCT FROM plugin_instance_id) "
        "AND current_user = 'trusted_grant_writer' "
        "AND current_setting('app.trusted_writer', true) = 'true' "
        "AND writer_generation = current_setting('app.writer_epoch', true)::bigint "
        "AND writer_generation = (SELECT writer_epoch FROM dynamic_app_capability_writer_epochs "
        "WHERE tenant_id = dynamic_app_capability_grants.tenant_id "
        "AND app_definition_id = dynamic_app_capability_grants.app_definition_id "
        "AND scope_kind = dynamic_app_capability_grants.scope_kind "
        "AND scope_id = dynamic_app_capability_grants.scope_id))",
    ),
    "audit": (
        "ALTER TABLE dynamic_app_capability_audit ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE dynamic_app_capability_audit FORCE ROW LEVEL SECURITY",
        "CREATE POLICY m06_cap_audit_tenant_read ON dynamic_app_capability_audit "
        "USING (current_setting('app.tenant_id', true) IS NOT NULL AND tenant_id = current_setting('app.tenant_id', true) "
        "AND current_setting('app.tenant_id', true) <> '' AND tenant_id IS NOT NULL "
        "AND EXISTS (SELECT 1 FROM m04e_dynamic_app_definitions d "
        "WHERE d.id = dynamic_app_capability_audit.app_definition_id AND d.tenant_id = dynamic_app_capability_audit.tenant_id))",
        "CREATE POLICY m06_cap_audit_trusted_append ON dynamic_app_capability_audit "
        "WITH CHECK (current_user = 'trusted_audit_writer' AND current_setting('app.audit_writer', true) = 'true' "
        "AND (tenant_id IS NULL OR (tenant_id = current_setting('app.tenant_id', true) "
        "AND EXISTS (SELECT 1 FROM m04e_dynamic_app_definitions d "
        "WHERE d.id = dynamic_app_capability_audit.app_definition_id AND d.tenant_id = dynamic_app_capability_audit.tenant_id))) "
        "AND (tenant_id IS NOT NULL OR (current_user IN ('migration_role', 'security_role') "
        "AND current_setting('app.migration_role', true) = 'true')))",
    ),
    "mapping": (
        "ALTER TABLE dynamic_app_capability_grant_mappings ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE dynamic_app_capability_grant_mappings FORCE ROW LEVEL SECURITY",
        "CREATE POLICY m06_cap_mapping_migration_write ON dynamic_app_capability_grant_mappings "
        "USING (current_user IN ('migration_role', 'security_role') "
        "AND current_setting('app.migration_role', true) = 'true') "
        "WITH CHECK (current_user IN ('migration_role', 'security_role') "
        "AND current_setting('app.migration_role', true) = 'true')",
    ),
    "quarantine": (
        "ALTER TABLE dynamic_app_capability_grant_quarantine ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE dynamic_app_capability_grant_quarantine FORCE ROW LEVEL SECURITY",
        "CREATE POLICY m06_cap_quarantine_security_write ON dynamic_app_capability_grant_quarantine "
        "USING (current_user IN ('migration_role', 'security_role') "
        "AND current_setting('app.migration_role', true) = 'true') "
        "WITH CHECK (current_user IN ('migration_role', 'security_role') "
        "AND current_setting('app.migration_role', true) = 'true')",
    ),
}

# Privilege inventory is a review artifact for the later revision.  API roles
# intentionally have no audit mutation privileges.
M06_PRIVILEGE_INVENTORY = (
    "REVOKE UPDATE, DELETE ON dynamic_app_capability_audit FROM PUBLIC, api_role",
    "REVOKE INSERT, UPDATE, DELETE ON dynamic_app_capability_grants FROM PUBLIC, api_role",
    "GRANT INSERT ON dynamic_app_capability_audit TO trusted_audit_writer",
    "GRANT INSERT, UPDATE ON dynamic_app_capability_grants TO trusted_grant_writer",
)

__all__ = [
    "CanonicalGrantAuditRow",
    "CanonicalGrantMappingRow",
    "CanonicalGrantQuarantineRow",
    "CanonicalGrantRow",
    "CanonicalGrantWriterEpochRow",
    "M06CanonicalGrantPromotionEvidenceRow",
    "M06_ACCEPTANCE_CASES",
    "M06_DEPENDENCY_BLOCKERS",
    "M06_PRIVILEGE_INVENTORY",
    "M06_ROLLBACK_GATE",
    "RLS_POLICY_DDL",
]
