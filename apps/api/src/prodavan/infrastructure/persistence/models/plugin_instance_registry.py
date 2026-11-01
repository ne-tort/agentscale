"""M05 exact plugin-instance identity and staged registry evidence foundation.

Rows in this module are migration evidence only. Positive instance resolution and
writes remain disabled until M04 scope and M04.e definition/version gates pass,
and both staged FK directions are validated.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


_INSTANCE_STATES = "('draft', 'published', 'disabled', 'revoked', 'quarantined')"
_SCOPE_KINDS = "('tenant', 'cabinet', 'project', 'plugin_instance')"
_PROMOTION_STATES = "('blocked', 'quarantined', 'ready', 'promotable')"


class PluginInstanceRegistryRow(Base):
    """Exact installation identity with late-validated reciprocal scope links."""

    __tablename__ = "m05_plugin_instances"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "definition_id",
            "version_id",
            "tenant_id",
            "scope_kind",
            "scope_id",
            name="uq_m05_plugin_instance_exact_identity",
        ),
        ForeignKeyConstraint(
            ["definition_id", "tenant_id"],
            ["m04e_dynamic_app_definitions.id", "m04e_dynamic_app_definitions.tenant_id"],
            name="fk_m05_instance_definition_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["version_id", "definition_id"],
            ["m04e_dynamic_app_versions.id", "m04e_dynamic_app_versions.definition_id"],
            name="fk_m05_instance_version_definition",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scope_registry_id"],
            ["scope_registry.id"],
            name="fk_m05_instance_registry_link",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        CheckConstraint(f"scope_kind IN {_SCOPE_KINDS}", name="ck_m05_instance_scope_kind"),
        CheckConstraint(f"lifecycle IN {_INSTANCE_STATES}", name="ck_m05_instance_lifecycle"),
        CheckConstraint(
            "artifact_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_m05_instance_artifact_sha256",
        ),
        CheckConstraint(
            "runtime_generation >= 0 AND writer_generation >= 0",
            name="ck_m05_instance_generations_nonnegative",
        ),
        CheckConstraint(
            "(scope_kind = 'tenant' AND scope_id = tenant_id AND cabinet_id IS NULL AND project_id IS NULL) "
            "OR (scope_kind = 'cabinet' AND scope_id = cabinet_id AND cabinet_id IS NOT NULL AND project_id IS NULL) "
            "OR (scope_kind = 'project' AND scope_id = project_id AND cabinet_id IS NOT NULL AND project_id IS NOT NULL) "
            "OR (scope_kind = 'plugin_instance' AND scope_id = id)",
            name="ck_m05_instance_typed_scope_shape",
        ),
        CheckConstraint(
            "lifecycle <> 'published' OR "
            "(artifact_digest IS NOT NULL AND subject_id IS NOT NULL AND subject_id <> '' "
            "AND session_id IS NOT NULL AND session_id <> '' AND scope_registry_id IS NOT NULL)",
            name="ck_m05_instance_published_evidence_required",
        ),
        Index("ix_m05_instance_tenant_scope_lifecycle", "tenant_id", "scope_kind", "scope_id", "lifecycle"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    definition_id: Mapped[str] = mapped_column(String(80), nullable=False)
    version_id: Mapped[str] = mapped_column(String(80), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(40), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(40), nullable=False)
    cabinet_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    scope_registry_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    artifact_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    subject_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    lifecycle: Mapped[str] = mapped_column(String(16), nullable=False, default="quarantined", server_default="quarantined")
    runtime_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    writer_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    checkpoint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PluginInstanceRegistryLinkRow(Base):
    """Staged reciprocal registry link; never an authorization-resolution row."""

    __tablename__ = "m05_plugin_instance_registry_links"
    __table_args__ = (
        UniqueConstraint("plugin_instance_id", name="uq_m05_registry_link_instance"),
        ForeignKeyConstraint(
            ["plugin_instance_id"],
            ["m05_plugin_instances.id"],
            name="fk_m05_registry_instance_link",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        CheckConstraint("owner_state IN ('active', 'archived', 'quarantined')", name="ck_m05_registry_link_state"),
        CheckConstraint(
            "scope_kind IN ('tenant', 'cabinet', 'project', 'plugin_instance')",
            name="ck_m05_registry_link_scope_kind",
        ),
        CheckConstraint(
            "(scope_kind = 'tenant' AND scope_id = tenant_id AND cabinet_id IS NULL AND project_id IS NULL) "
            "OR (scope_kind = 'cabinet' AND scope_id = cabinet_id AND cabinet_id IS NOT NULL AND project_id IS NULL) "
            "OR (scope_kind = 'project' AND scope_id = project_id AND cabinet_id IS NOT NULL AND project_id IS NOT NULL) "
            "OR (scope_kind = 'plugin_instance' AND scope_id = plugin_instance_id)",
            name="ck_m05_registry_link_typed_shape",
        ),
        Index("ix_m05_registry_link_tenant_state", "tenant_id", "owner_state"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    plugin_instance_id: Mapped[str] = mapped_column(String(40), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(40), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(40), nullable=False)
    cabinet_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    owner_state: Mapped[str] = mapped_column(String(16), nullable=False, default="quarantined", server_default="quarantined")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class M05PluginInstancePromotionEvidenceRow(Base):
    """Task-owned immutable-by-policy acceptance and promotion gate record."""

    __tablename__ = "m05_plugin_instance_promotion_evidence"
    __table_args__ = (
        UniqueConstraint("tenant_id", "definition_id", "partition_id", "revision_id", name="uq_m05_instance_promotion_revision"),
        CheckConstraint(f"outcome IN {_PROMOTION_STATES}", name="ck_m05_instance_promotion_outcome"),
        CheckConstraint("canonical_positive_writes = false", name="ck_m05_instance_no_positive_writes"),
        CheckConstraint("source_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_m05_instance_promotion_source_sha256"),
        CheckConstraint("target_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_m05_instance_promotion_target_sha256"),
        CheckConstraint("accepted_count >= 0 AND quarantined_count >= 0 AND archived_count >= 0", name="ck_m05_instance_promotion_counts_nonnegative"),
        Index("ix_m05_instance_promotion_partition_time", "tenant_id", "definition_id", "partition_id", "recorded_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), nullable=False)
    definition_id: Mapped[str] = mapped_column(String(80), nullable=False)
    partition_id: Mapped[str] = mapped_column(String(128), nullable=False)
    revision_id: Mapped[str] = mapped_column(String(80), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="blocked", server_default="blocked")
    source_snapshot_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    target_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    composite_key_evidence: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reciprocal_fk_evidence: Mapped[dict] = mapped_column(JSONB, nullable=False)
    quarantine_counts: Mapped[dict] = mapped_column(JSONB, nullable=False)
    lifecycle_distribution: Mapped[dict] = mapped_column(JSONB, nullable=False)
    dependency_digests: Mapped[dict] = mapped_column(JSONB, nullable=False)
    checkpoint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    gate_evidence: Mapped[dict] = mapped_column(JSONB, nullable=False)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    quarantined_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    archived_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    canonical_positive_writes: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    writer_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    fencing_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reviewer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    previous_revision_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    rollback_gate: Mapped[str] = mapped_column(Text, nullable=False, default="legacy owner remains authoritative; preserve evidence and repair forward", server_default="legacy owner remains authoritative; preserve evidence and repair forward")


# Review inputs only; no tool execution or claim of data validation is implied.
M05_ACCEPTANCE_CASES = (
    "duplicate_exact_instance_identity_is_rejected",
    "cross_tenant_scope_tuple_is_rejected_or_quarantined",
    "definition_version_mismatch_is_rejected",
    "artifact_digest_mismatch_is_quarantined",
    "missing_registry_link_is_not_positive",
    "both_reciprocal_fk_directions_require_validation",
    "invalid_typed_scope_shape_is_rejected",
    "unresolved_m04_scope_or_m04e_dependency_keeps_partition_blocked",
    "legacy_published_state_is_not_mapped_to_active_without_review",
)

M05_STAGED_CONSTRAINTS = {
    "exact_identity_key": "(id, definition_id, version_id, tenant_id, scope_kind, scope_id)",
    "instance_to_registry": "nullable scope_registry_id plus deferred RESTRICT FK during staging; mandatory before positive resolution",
    "registry_to_instance": "deferred RESTRICT FK by exact plugin_instance_id; validate only after exact instance import",
    "positive_path": "disabled until M04-02, M04E-02, both FK validations, artifact equality and lifecycle policy evidence pass",
}

M05_PROMOTION_RECORD = {
    "default_outcome": "blocked",
    "canonical_positive_writes": False,
    "blockers": ("MIG-M04-02 incomplete", "MIG-M04E-02 incomplete", "scope_registry lacks definition/version anchors for full reciprocal composite proof"),
    "rollback": "before import drop only empty staged objects; after import halt partition, preserve evidence and legacy authority, repair forward",
}

__all__ = [
    "M05_ACCEPTANCE_CASES",
    "M05_PROMOTION_RECORD",
    "M05_STAGED_CONSTRAINTS",
    "M05PluginInstancePromotionEvidenceRow",
    "PluginInstanceRegistryLinkRow",
    "PluginInstanceRegistryRow",
]
