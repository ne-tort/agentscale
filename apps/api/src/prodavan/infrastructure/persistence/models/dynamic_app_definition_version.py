"""M04.e target definition/version persistence foundation.

These rows are migration evidence and target identity, not authorization state.
Positive grant/instance writes remain disabled until parent scope, RLS, artifact
immutability, and staged-constraint validation gates are separately proven.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, SupportsIndex

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    event,
    func,
    inspect as sa_inspect,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


_DEFINITION_STATES = "('draft', 'published', 'disabled', 'revoked')"
_VERSION_STATES = "('draft', 'published', 'disabled', 'revoked')"
_PROMOTION_STATES = "('blocked', 'quarantined')"


class _FrozenDict(dict):
    """JSON object that prevents in-place mutation of a version snapshot."""

    def __setitem__(self, key, value) -> None:
        raise TypeError("version snapshots are immutable")

    def __delitem__(self, key) -> None:
        raise TypeError("version snapshots are immutable")

    def clear(self) -> None:
        raise TypeError("version snapshots are immutable")

    def pop(self, key, default=None):
        raise TypeError("version snapshots are immutable")

    def popitem(self):
        raise TypeError("version snapshots are immutable")

    def setdefault(self, key, default=None):
        raise TypeError("version snapshots are immutable")

    def update(self, *args, **kwargs) -> None:
        raise TypeError("version snapshots are immutable")

    def __ior__(self, other):
        raise TypeError("version snapshots are immutable")


class _FrozenList(list):
    """JSON array that prevents in-place mutation of a version snapshot."""

    def __setitem__(self, index, value) -> None:
        raise TypeError("version snapshots are immutable")

    def __delitem__(self, index) -> None:
        raise TypeError("version snapshots are immutable")

    def append(self, value) -> None:
        raise TypeError("version snapshots are immutable")

    def clear(self) -> None:
        raise TypeError("version snapshots are immutable")

    def extend(self, values) -> None:
        raise TypeError("version snapshots are immutable")

    def insert(self, index, value) -> None:
        raise TypeError("version snapshots are immutable")

    def pop(self, index: SupportsIndex = -1):
        raise TypeError("version snapshots are immutable")

    def remove(self, value) -> None:
        raise TypeError("version snapshots are immutable")

    def reverse(self) -> None:
        raise TypeError("version snapshots are immutable")

    def sort(self, *, key=None, reverse=False) -> None:
        raise TypeError("version snapshots are immutable")

    def __iadd__(self, values):
        raise TypeError("version snapshots are immutable")

    def __imul__(self, count):
        raise TypeError("version snapshots are immutable")


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return _FrozenDict({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return _FrozenList(_freeze_json(item) for item in value)
    return value


class _ImmutableJSONB(TypeDecorator):
    impl = JSONB
    cache_ok = True

    def process_bind_param(self, value: Any, dialect) -> Any:
        return value

    def process_result_value(self, value: Any, dialect) -> Any:
        return _freeze_json(value)


_IMMUTABLE_JSONB = _ImmutableJSONB()


def _freeze_assigned_json(_target, value, _oldvalue, _initiator):
    return _freeze_json(value)


_DEFINITION_IMMUTABLE_FIELDS = (
    "id",
    "tenant_id",
    "app_id",
    "scope_kind",
    "scope_id",
    "display_name",
    "description",
    "source_snapshot_id",
    "source_digest",
    "created_at",
)
_VERSION_IMMUTABLE_FIELDS = (
    "id",
    "definition_id",
    "tenant_id",
    "app_id",
    "manifest_version",
    "artifact_digest",
    "artifact_uri",
    "manifest",
    "requested_capabilities",
    "source_snapshot_id",
    "source_digest",
    "created_at",
)


def _reject_immutable_update(target: Any, connection, fields: tuple[str, ...], owner: str) -> None:
    """Compare ORM state with stored values so in-place JSON mutations are caught."""
    state = sa_inspect(target)
    identity = state.identity
    if identity is None:
        return
    table = target.__table__
    stored = connection.execute(
        select(*(table.c[name] for name in fields)).where(table.c.id == identity[0])
    ).mappings().one_or_none()
    if stored is None:
        return
    changed = tuple(name for name in fields if stored[name] != getattr(target, name))
    if changed:
        names = ", ".join(changed)
        raise ValueError(f"{owner} immutable fields cannot be updated: {names}")


class DynamicAppDefinitionFoundationRow(Base):
    """Canonical target definition identity; legacy tables remain authoritative."""

    __tablename__ = "m04e_dynamic_app_definitions"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_m04e_definition_id_tenant"),
        UniqueConstraint("id", "app_id", name="uq_m04e_definition_id_app"),
        UniqueConstraint("tenant_id", "app_id", name="uq_m04e_definition_tenant_app"),
        CheckConstraint(
            "length(btrim(tenant_id)) > 0",
            name="ck_m04e_definition_tenant_nonempty",
        ),
        CheckConstraint(
            "length(btrim(app_id)) > 0",
            name="ck_m04e_definition_app_id_format",
        ),
        CheckConstraint(
            "length(btrim(display_name)) > 0",
            name="ck_m04e_definition_display_name_nonempty",
        ),
        CheckConstraint(
            f"lifecycle IN {_DEFINITION_STATES}",
            name="ck_m04e_definition_lifecycle",
        ),
        CheckConstraint(
            "scope_kind IN ('tenant', 'cabinet', 'project')",
            name="ck_m04e_definition_scope_kind",
        ),
        CheckConstraint(
            "scope_kind <> 'tenant' OR scope_id = tenant_id",
            name="ck_m04e_definition_tenant_scope_identity",
        ),
        CheckConstraint("scope_id <> ''", name="ck_m04e_definition_scope_nonempty"),
        CheckConstraint("updated_at >= created_at", name="ck_m04e_definition_updated_at_order"),
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
    display_name: Mapped[str] = mapped_column(String(260), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(40), nullable=False)
    lifecycle: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", server_default="draft"
    )
    current_version_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_snapshot_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DynamicAppDefinitionVersionFoundationRow(Base):
    """Version snapshot with deterministic manifest and same-definition identity."""

    __tablename__ = "m04e_dynamic_app_versions"
    __table_args__ = (
        UniqueConstraint(
            "definition_id", "manifest_version", name="uq_m04e_version_definition_manifest"
        ),
        UniqueConstraint("id", "definition_id", name="uq_m04e_version_id_definition"),
        UniqueConstraint(
            "definition_id", "artifact_digest", name="uq_m04e_version_definition_artifact"
        ),
        UniqueConstraint(
            "id", "definition_id", "artifact_digest",
            name="uq_m04e_version_id_definition_artifact",
        ),
        CheckConstraint(
            "manifest_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+(-[0-9A-Za-z.-]+)?$'",
            name="ck_m04e_version_manifest_semver",
        ),
        CheckConstraint(
            "artifact_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_m04e_version_artifact_sha256",
        ),
        CheckConstraint(
            f"lifecycle IN {_VERSION_STATES}",
            name="ck_m04e_version_lifecycle",
        ),
        CheckConstraint(
            "COALESCE(manifest->>'protocol' = '1.0', false)",
            name="ck_m04e_version_manifest_protocol",
        ),
        CheckConstraint(
            "COALESCE(manifest->>'version' = manifest_version, false)",
            name="ck_m04e_version_manifest_version_match",
        ),
        CheckConstraint(
            "COALESCE(manifest->>'appId' = app_id, false)",
            name="ck_m04e_version_manifest_app_id_match",
        ),
        CheckConstraint(
            "COALESCE(manifest #>> '{entrypoints,plugin,artifact,sha256}' IS NULL "
            "OR manifest #>> '{entrypoints,plugin,artifact,sha256}' = substring(artifact_digest FROM 8), false)",
            name="ck_m04e_version_manifest_artifact_digest_match",
        ),
        CheckConstraint(
            "jsonb_typeof(manifest) = 'object' "
            "AND COALESCE(jsonb_typeof(manifest->'appId') = 'string' "
            "AND manifest->>'appId' ~ '^[a-z][a-z0-9-]{2,63}$', false) "
            "AND COALESCE(jsonb_typeof(manifest->'displayName') = 'string' "
            "AND length(btrim(manifest->>'displayName')) > 0, false) "
            "AND COALESCE(jsonb_typeof(manifest->'entrypoints') = 'object' "
            "AND manifest->'entrypoints' <> '{}'::jsonb, false) "
            "AND COALESCE(jsonb_typeof(manifest->'requestedCapabilities') = 'array', false)",
            name="ck_m04e_version_manifest_shape",
        ),
        CheckConstraint(
            "COALESCE(jsonb_typeof(manifest->'requestedCapabilities') = 'array', false) "
            "AND requested_capabilities = manifest->'requestedCapabilities'",
            name="ck_m04e_version_requested_capabilities_match",
        ),
        CheckConstraint(
            "lifecycle = 'published' OR published_at IS NULL",
            name="ck_m04e_version_published_at_lifecycle",
        ),
        CheckConstraint(
            "lifecycle <> 'published' OR published_at IS NOT NULL",
            name="ck_m04e_version_published_lifecycle_timestamp",
        ),
        CheckConstraint(
            "source_snapshot_id <> '' AND source_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_m04e_version_source_proof",
        ),
        ForeignKeyConstraint(
            ["definition_id", "tenant_id"],
            ["m04e_dynamic_app_definitions.id", "m04e_dynamic_app_definitions.tenant_id"],
            name="fk_m04e_version_definition_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["definition_id", "app_id"],
            ["m04e_dynamic_app_definitions.id", "m04e_dynamic_app_definitions.app_id"],
            name="fk_m04e_version_definition_app",
            ondelete="RESTRICT",
        ),
        Index("ix_m04e_version_definition_lifecycle", "definition_id", "lifecycle"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    definition_id: Mapped[str] = mapped_column(String(80), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(40), nullable=False)
    app_id: Mapped[str] = mapped_column(String(160), nullable=False)
    manifest_version: Mapped[str] = mapped_column(String(128), nullable=False)
    artifact_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    artifact_uri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    manifest: Mapped[dict] = mapped_column(_IMMUTABLE_JSONB, nullable=False)
    requested_capabilities: Mapped[list] = mapped_column(_IMMUTABLE_JSONB, nullable=False)
    lifecycle: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", server_default="draft"
    )
    source_snapshot_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


@event.listens_for(DynamicAppDefinitionVersionFoundationRow.manifest, "set", retval=True)
def _freeze_manifest_on_assignment(_target, value, _oldvalue, _initiator):
    return _freeze_assigned_json(_target, value, _oldvalue, _initiator)


@event.listens_for(
    DynamicAppDefinitionVersionFoundationRow.requested_capabilities, "set", retval=True
)
def _freeze_requested_capabilities_on_assignment(_target, value, _oldvalue, _initiator):
    return _freeze_assigned_json(_target, value, _oldvalue, _initiator)


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


@event.listens_for(DynamicAppDefinitionFoundationRow, "before_update")
def _definition_immutable_update_guard(_mapper, connection, target) -> None:
    _reject_immutable_update(
        target,
        connection,
        _DEFINITION_IMMUTABLE_FIELDS,
        "definition identity and source proof",
    )


@event.listens_for(DynamicAppDefinitionVersionFoundationRow, "before_update")
def _version_immutable_update_guard(_mapper, connection, target) -> None:
    _reject_immutable_update(
        target,
        connection,
        _VERSION_IMMUTABLE_FIELDS,
        "version and artifact snapshot",
    )


# Static acceptance artifacts owned by MIG-M04E-01. These are review inputs,
# not executed checks or claims that historical data has passed validation.
M04E_ACCEPTANCE_CASES = (
    "orphan_definition_or_version_is_quarantined",
    "definition_tenant_mismatch_is_rejected_by_company_fk",
    "version_tenant_mismatch_is_rejected_by_composite_definition_fk",
    "cross_definition_current_version_link_is_rejected_by_composite_fk",
    "duplicate_manifest_version_within_definition_is_rejected",
    "same_manifest_version_across_definitions_is_allowed",
    "same_artifact_digest_within_definition_is_rejected",
    "artifact_digest_and_manifest_mutation_conflicts_in_orm_guard",
    "direct_sql_artifact_mutation_requires_database_trigger_privilege_gate",
    "invalid_definition_or_version_lifecycle_is_rejected",
    "published_definition_without_current_version_is_rejected",
    "missing_or_cross_definition_current_version_is_rejected",
    "published_definition_current_version_must_be_published_gate",
    "current_version_cycle_is_staged_and_late_validated",
    "manifest_identity_and_requested_capabilities_must_match",
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
