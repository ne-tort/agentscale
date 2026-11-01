"""M04.e target definition/version persistence foundation.

These rows are migration evidence and target identity, not authorization state.
Positive grant/instance writes remain disabled until parent scope, RLS, artifact
immutability, and staged-constraint validation gates are separately proven.
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


_DEFINITION_STATES = "('draft', 'published', 'disabled', 'revoked', 'archived', 'quarantined')"
_VERSION_STATES = "('draft', 'published', 'disabled', 'revoked', 'archived', 'quarantined')"
_PROMOTION_STATES = "('blocked', 'quarantined', 'ready', 'promotable')"


class DynamicAppDefinitionFoundationRow(Base):
    """Canonical target definition identity; legacy tables remain authoritative."""

    __tablename__ = "m04e_dynamic_app_definitions"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_m04e_definition_id_tenant"),
        UniqueConstraint("tenant_id", "app_id", name="uq_m04e_definition_tenant_app"),
        CheckConstraint("tenant_id <> ''", name="ck_m04e_definition_tenant_nonempty"),
        CheckConstraint("app_id <> ''", name="ck_m04e_definition_app_nonempty"),
        CheckConstraint(f"lifecycle IN {_DEFINITION_STATES}", name="ck_m04e_definition_lifecycle"),
        CheckConstraint(
            "lifecycle <> 'published' OR current_version_id IS NOT NULL",
            name="ck_m04e_definition_published_has_current",
        ),
        CheckConstraint(
            "source_snapshot_id <> '' AND source_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_m04e_definition_source_proof",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["companies.id"],
            name="fk_m04e_definition_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["current_version_id", "id"],
            ["m04e_dynamic_app_versions.id", "m04e_dynamic_app_versions.definition_id"],
            name="fk_m04e_definition_current_version_same_definition",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        Index("ix_m04e_definition_tenant_lifecycle", "tenant_id", "lifecycle"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), nullable=False)
    app_id: Mapped[str] = mapped_column(String(160), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(80), nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    current_version_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_snapshot_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DynamicAppDefinitionVersionFoundationRow(Base):
    """Version snapshot with deterministic manifest and same-definition identity."""

    __tablename__ = "m04e_dynamic_app_versions"
    __table_args__ = (
        UniqueConstraint("definition_id", "manifest_version", name="uq_m04e_version_definition_manifest"),
        UniqueConstraint("id", "definition_id", name="uq_m04e_version_id_definition"),
        CheckConstraint("manifest_version <> ''", name="ck_m04e_version_manifest_nonempty"),
        CheckConstraint("artifact_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_m04e_version_artifact_sha256"),
        CheckConstraint(f"lifecycle IN {_VERSION_STATES}", name="ck_m04e_version_lifecycle"),
        CheckConstraint("source_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_m04e_version_source_sha256"),
        ForeignKeyConstraint(
            ["definition_id"],
            ["m04e_dynamic_app_definitions.id"],
            name="fk_m04e_version_definition",
            ondelete="RESTRICT",
        ),
        Index("ix_m04e_version_definition_lifecycle", "definition_id", "lifecycle"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    definition_id: Mapped[str] = mapped_column(String(80), nullable=False)
    manifest_version: Mapped[str] = mapped_column(String(128), nullable=False)
    artifact_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    artifact_uri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    source_snapshot_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class M04EDefinitionVersionPromotionEvidenceRow(Base):
    """Immutable-by-policy gate record; never enables a positive writer."""

    __tablename__ = "m04e_definition_version_promotion_evidence"
    __table_args__ = (
        UniqueConstraint("tenant_id", "definition_id", "revision_id", name="uq_m04e_promotion_revision"),
        CheckConstraint(f"outcome IN {_PROMOTION_STATES}", name="ck_m04e_promotion_outcome"),
        CheckConstraint("canonical_positive_writes = false", name="ck_m04e_no_positive_writes"),
        CheckConstraint("source_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_m04e_promotion_source_sha256"),
        CheckConstraint("target_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_m04e_promotion_target_sha256"),
        CheckConstraint("current_version_resolution_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_m04e_promotion_current_resolution_sha256"),
        CheckConstraint("artifact_equality_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_m04e_promotion_artifact_equality_sha256"),
        CheckConstraint("valid_count >= 0 AND archived_count >= 0 AND quarantined_count >= 0", name="ck_m04e_promotion_counts_nonnegative"),
        ForeignKeyConstraint(
            ["definition_id", "tenant_id"],
            ["m04e_dynamic_app_definitions.id", "m04e_dynamic_app_definitions.tenant_id"],
            name="fk_m04e_promotion_definition_tenant",
            ondelete="RESTRICT",
        ),
        Index("ix_m04e_promotion_partition_time", "tenant_id", "definition_id", "recorded_at"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), nullable=False)
    definition_id: Mapped[str] = mapped_column(String(80), nullable=False)
    revision_id: Mapped[str] = mapped_column(String(80), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    source_snapshot_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    target_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    valid_count: Mapped[int] = mapped_column(Integer, nullable=False)
    archived_count: Mapped[int] = mapped_column(Integer, nullable=False)
    quarantined_count: Mapped[int] = mapped_column(Integer, nullable=False)
    current_version_resolution_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    artifact_equality_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    gate_evidence: Mapped[dict] = mapped_column(JSONB, nullable=False)
    canonical_positive_writes: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    reviewer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    previous_revision_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


# Static acceptance artifacts owned by MIG-M04E-01. These are review inputs,
# not executed checks or claims that historical data has passed validation.
M04E_ACCEPTANCE_CASES = (
    "orphan_definition_or_version_is_quarantined",
    "cross_tenant_definition_is_rejected",
    "cross_definition_version_link_is_rejected",
    "duplicate_manifest_version_within_definition_is_rejected",
    "same_manifest_version_across_definitions_is_allowed",
    "changed_artifact_digest_is_conflict_not_update",
    "invalid_lifecycle_is_rejected",
    "published_definition_requires_same_definition_current_version",
    "current_version_cycle_is_staged_and_late_validated",
    "promotion_records_keep_canonical_positive_writes_false",
)

M04E_ROLLBACK_GATE = {
    "before_evidence_import": "remove only empty new constraints after dependency review",
    "after_evidence_import": "stop mapping and repair forward; preserve evidence and legacy reads/writes",
    "ready_requires": "zero unresolved required rows and deterministic definition/manifest resolution",
    "default_outcome": "blocked",
}


__all__ = [
    "DynamicAppDefinitionFoundationRow",
    "DynamicAppDefinitionVersionFoundationRow",
    "M04EDefinitionVersionPromotionEvidenceRow",
    "M04E_ACCEPTANCE_CASES",
    "M04E_ROLLBACK_GATE",
]
