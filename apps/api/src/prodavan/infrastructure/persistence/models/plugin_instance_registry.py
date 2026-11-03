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
    event,
    func,
    inspect as sa_inspect,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base
from prodavan.infrastructure.persistence.models.dynamic_app_definition_version import (
    DynamicAppDefinitionVersionFoundationRow,
)
from prodavan.infrastructure.persistence.models.owner_registry import ScopeRegistryRow


_INSTANCE_STATES = "('draft', 'published', 'disabled', 'revoked', 'quarantined')"
_SCOPE_KINDS = "('plugin_instance')"
_PROMOTION_STATES = "('blocked', 'quarantined')"


def _append_unique_key(table, columns: tuple[str, ...], name: str) -> None:
    """Register a named composite FK target key without changing its owner file."""
    if any(
        isinstance(constraint, UniqueConstraint)
        and tuple(column.name for column in constraint.columns) == columns
        for constraint in table.constraints
    ):
        return
    table.append_constraint(UniqueConstraint(*columns, name=name))


_append_unique_key(
    DynamicAppDefinitionVersionFoundationRow.__table__,
    ("id", "definition_id", "artifact_digest"),
    "uq_m04e_version_id_definition_artifact",
)
for _columns, _name in (
    (("id", "plugin_instance_id", "tenant_id"), "uq_scope_registry_m05_instance_tenant"),
    (
        ("id", "plugin_instance_id", "tenant_id", "cabinet_id"),
        "uq_scope_registry_m05_instance_cabinet",
    ),
    (
        ("id", "plugin_instance_id", "tenant_id", "cabinet_id", "project_id"),
        "uq_scope_registry_m05_instance_project",
    ),
):
    _append_unique_key(ScopeRegistryRow.__table__, _columns, _name)


class PluginInstanceRegistryRow(Base):
    """Exact instance identity with verbatim source provenance and staged scope links."""

    __tablename__ = "m05_plugin_instances"
    __table_args__ = (
        UniqueConstraint(
            "plugin_instance_id",
            "definition_id",
            "version_id",
            "tenant_id",
            "scope_kind",
            "scope_id",
            name="uq_m05_plugin_instance_exact_identity",
        ),
        UniqueConstraint(
            "id",
            "definition_id",
            "version_id",
            "tenant_id",
            "scope_kind",
            "scope_id",
            name="uq_m05_plugin_instance_grant_identity",
        ),
        UniqueConstraint(
            "source_kind",
            "source_id",
            "source_schema_version",
            name="uq_m05_instance_source_identity",
        ),
        UniqueConstraint(
            "scope_registry_id",
            "plugin_instance_id",
            "tenant_id",
            name="uq_m05_instance_registry_tenant_link",
        ),
        UniqueConstraint(
            "scope_registry_id",
            "plugin_instance_id",
            "tenant_id",
            "cabinet_id",
            name="uq_m05_instance_registry_cabinet_link",
        ),
        UniqueConstraint(
            "scope_registry_id",
            "plugin_instance_id",
            "tenant_id",
            "cabinet_id",
            "project_id",
            name="uq_m05_instance_registry_project_link",
        ),
        ForeignKeyConstraint(
            ["definition_id", "tenant_id"],
            ["m04e_dynamic_app_definitions.id", "m04e_dynamic_app_definitions.tenant_id"],
            name="fk_m05_instance_definition_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["version_id", "definition_id", "artifact_digest"],
            [
                "m04e_dynamic_app_versions.id",
                "m04e_dynamic_app_versions.definition_id",
                "m04e_dynamic_app_versions.artifact_digest",
            ],
            name="fk_m05_instance_version_definition_artifact",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scope_registry_id", "plugin_instance_id", "tenant_id"],
            [
                "scope_registry.id",
                "scope_registry.plugin_instance_id",
                "scope_registry.tenant_id",
            ],
            name="fk_m05_instance_registry_tenant_link",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["scope_registry_id", "plugin_instance_id", "tenant_id", "cabinet_id"],
            [
                "scope_registry.id",
                "scope_registry.plugin_instance_id",
                "scope_registry.tenant_id",
                "scope_registry.cabinet_id",
            ],
            name="fk_m05_instance_registry_cabinet_link",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["scope_registry_id", "plugin_instance_id", "tenant_id", "cabinet_id", "project_id"],
            [
                "scope_registry.id",
                "scope_registry.plugin_instance_id",
                "scope_registry.tenant_id",
                "scope_registry.cabinet_id",
                "scope_registry.project_id",
            ],
            name="fk_m05_instance_registry_project_link",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        CheckConstraint(f"scope_kind IN {_SCOPE_KINDS}", name="ck_m05_instance_scope_kind"),
        CheckConstraint(f"lifecycle IN {_INSTANCE_STATES}", name="ck_m05_instance_lifecycle"),
        CheckConstraint("plugin_instance_id = id", name="ck_m05_instance_exact_public_id"),
        CheckConstraint(
            "plugin_instance_id <> '' AND plugin_instance_id = btrim(plugin_instance_id) "
            "AND plugin_instance_id !~ '[^A-Za-z0-9_.:-]'",
            name="ck_m05_instance_id_canonical",
        ),
        CheckConstraint(
            "tenant_id <> '' AND tenant_id = btrim(tenant_id) "
            "AND tenant_id !~ '[^A-Za-z0-9_.:-]'",
            name="ck_m05_instance_tenant_canonical_id",
        ),
        CheckConstraint(
            "scope_id <> '' AND scope_id = btrim(scope_id) "
            "AND scope_id !~ '[^A-Za-z0-9_.:-]'",
            name="ck_m05_instance_scope_canonical_id",
        ),
        CheckConstraint(
            "(cabinet_id IS NULL OR cabinet_id <> '') "
            "AND (project_id IS NULL OR project_id <> '')",
            name="ck_m05_instance_parent_scope_nonempty",
        ),
        CheckConstraint(
            "artifact_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_m05_instance_artifact_sha256",
        ),
        CheckConstraint(
            "runtime_generation >= 0 AND writer_generation >= 0",
            name="ck_m05_instance_generations_nonnegative",
        ),
        CheckConstraint(
            "scope_kind = 'plugin_instance' AND scope_id = plugin_instance_id "
            "AND (project_id IS NULL OR cabinet_id IS NOT NULL)",
            name="ck_m05_instance_typed_scope_shape",
        ),
        CheckConstraint(
            "lifecycle <> 'published' OR "
            "(subject_id IS NOT NULL AND subject_id <> '' "
            "AND session_id IS NOT NULL AND session_id <> '' AND scope_registry_id IS NOT NULL)",
            name="ck_m05_instance_published_evidence_required",
        ),
        CheckConstraint("length(btrim(source_kind)) > 0", name="ck_m05_instance_source_kind_nonempty"),
        CheckConstraint("length(btrim(source_id)) > 0", name="ck_m05_instance_source_id_nonempty"),
        CheckConstraint(
            "length(btrim(source_schema_version)) > 0",
            name="ck_m05_instance_source_schema_nonempty",
        ),
        CheckConstraint(
            "source_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_m05_instance_source_sha256",
        ),
        Index("ix_m05_instance_tenant_scope_lifecycle", "tenant_id", "scope_kind", "scope_id", "lifecycle"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    plugin_instance_id: Mapped[str] = mapped_column(String(40), nullable=False)
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
    # Preserve the source installation ID byte-for-byte. The unique source key
    # prevents duplicate mapping; database append-only enforcement remains a gate.
    source_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    source_id: Mapped[str] = mapped_column(String(80), nullable=False)
    source_schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    checkpoint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


@event.listens_for(PluginInstanceRegistryRow, "before_update")
def _reject_instance_identity_mutation(mapper, connection, target) -> None:
    """Keep source and canonical instance identifiers immutable via ORM writes."""
    state = sa_inspect(target)
    immutable_fields = (
        "id",
        "plugin_instance_id",
        "definition_id",
        "version_id",
        "tenant_id",
        "scope_kind",
        "scope_id",
        "cabinet_id",
        "project_id",
        "artifact_digest",
        "source_kind",
        "source_id",
        "source_schema_version",
        "source_digest",
    )
    if any(state.attrs[field].history.has_changes() for field in immutable_fields):
        raise ValueError("plugin instance identity and source provenance are immutable")


# M05 target keys for the owner-registry reciprocal anchor references.
for _columns, _name in (
    (("plugin_instance_id", "tenant_id"), "uq_m05_plugin_instance_tenant_anchor"),
    (
        ("plugin_instance_id", "tenant_id", "cabinet_id"),
        "uq_m05_plugin_instance_cabinet_anchor",
    ),
    (
        ("plugin_instance_id", "tenant_id", "cabinet_id", "project_id"),
        "uq_m05_plugin_instance_project_anchor",
    ),
):
    _append_unique_key(PluginInstanceRegistryRow.__table__, _columns, _name)

for _columns, _targets, _name in (
    (
        ("id", "plugin_instance_id", "tenant_id"),
        ("scope_registry_id", "plugin_instance_id", "tenant_id"),
        "fk_m05_registry_instance_tenant_link",
    ),
    (
        ("id", "plugin_instance_id", "tenant_id", "cabinet_id"),
        ("scope_registry_id", "plugin_instance_id", "tenant_id", "cabinet_id"),
        "fk_m05_registry_instance_cabinet_link",
    ),
    (
        ("id", "plugin_instance_id", "tenant_id", "cabinet_id", "project_id"),
        ("scope_registry_id", "plugin_instance_id", "tenant_id", "cabinet_id", "project_id"),
        "fk_m05_registry_instance_project_link",
    ),
):
    ScopeRegistryRow.__table__.append_constraint(
        ForeignKeyConstraint(
            list(_columns),
            [f"m05_plugin_instances.{column}" for column in _targets],
            name=_name,
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        )
    )


class PluginInstanceRegistryLinkRow(Base):
    """Staged reciprocal registry link; never an authorization-resolution row."""

    __tablename__ = "m05_plugin_instance_registry_links"
    __table_args__ = (
        UniqueConstraint("plugin_instance_id", name="uq_m05_registry_link_instance"),
        ForeignKeyConstraint(
            ["plugin_instance_id"],
            ["m05_plugin_instances.id"],
            name="fk_m05_registry_instance_link_evidence",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        CheckConstraint("owner_state IN ('active', 'archived', 'quarantined')", name="ck_m05_registry_link_state"),
        CheckConstraint(
            "scope_kind = 'plugin_instance' AND scope_id = plugin_instance_id",
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
    "source_instance_id_is_preserved_and_immutable",
    "legacy_source_id_over_canonical_width_requires_inventory_before_mapping",
)

M05_STAGED_CONSTRAINTS = {
    "exact_instance_key": "(plugin_instance_id, definition_id, version_id, tenant_id, scope_kind, scope_id)",
    "grant_compatible_key": "(id, definition_id, version_id, tenant_id, scope_kind, scope_id), with ck_m05_instance_exact_public_id enforcing id = plugin_instance_id",
    "immutable_source_identity": "unique (source_kind, source_id, source_schema_version); the source ID is retained separately and verbatim without derivation or truncation, while the full instance identity/provenance tuple is ORM-immutable; no source-to-canonical ID equality is assumed; bulk/raw SQL still requires a database privilege/trigger gate",
    "version_artifact_key": "(version_id, definition_id, artifact_digest) -> m04e_dynamic_app_versions(id, definition_id, artifact_digest); target key is M05 metadata-only",
    "instance_to_registry": "(scope_registry_id, plugin_instance_id, tenant_id[, cabinet_id[, project_id]]) -> scope_registry(id, plugin_instance_id, tenant_id[, cabinet_id[, project_id]]); nullable/deferred RESTRICT staging",
    "registry_to_instance": "scope_registry(id, plugin_instance_id, tenant_id[, cabinet_id[, project_id]]) -> m05_plugin_instances(scope_registry_id, plugin_instance_id, tenant_id[, cabinet_id[, project_id]]); deferred RESTRICT staging",
    "typed_anchor_status": "reciprocal scope FKs prove typed plugin-instance ownership only; scope_registry has no definition/version/artifact anchors, so M04E ownership remains a separate FK proof",
    "positive_path": "disabled until M04-02 and M04E-02 promotion, both FK validations, artifact equality, typed-anchor proof and lifecycle policy evidence pass",
}

M05_PROMOTION_RECORD = {
    "default_outcome": "blocked",
    "canonical_positive_writes": False,
    "blockers": (
        "MIG-M04-02 blocked: typed owner/scope mapping, RLS and accepted source mappings unavailable",
        "MIG-M04E-02 blocked: definition/version artifact proof and source handoff unavailable",
        "dynamic_apps.AppInstanceRow (dynamic_app_instances) has id String(80) with no max-40 check (its current generator emits ains_<16 hex chars>), while m05 plugin_instance_id/scope_id are String(40); its scope is limited to tenant/cabinet/project and it has no canonical tenant, artifact, subject/session, or generation anchors. No inventory proves all legacy IDs fit or an exact source-to-plugin ID mapping, so no FK to this legacy table is declared",
        "M05 InstanceImportCandidate accepts source_id only up to 40 characters, narrower than dynamic_app_instances.id String(80) and this target's verbatim source_id String(80); align the intake contract or prove the bounded source inventory before claiming lossless import",
        "source identity is preserved and ORM UPDATE is rejected, but bulk/raw SQL immutability needs append-only database privileges/trigger DDL outside this ORM-only task",
        "scope_registry has no definition/version/artifact anchors for full reciprocal composite proof",
        "reciprocal FK declarations are staged only and have not been validated",
    ),
    "rollback": "before import drop only empty staged objects; after import halt partition, preserve rows/evidence and legacy authority, repair forward",
}

__all__ = [
    "M05_ACCEPTANCE_CASES",
    "M05_PROMOTION_RECORD",
    "M05_STAGED_CONSTRAINTS",
    "M05PluginInstancePromotionEvidenceRow",
    "PluginInstanceRegistryLinkRow",
    "PluginInstanceRegistryRow",
]
