"""Canonical typed owner/scope registry foundation for migration evidence."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


def _id() -> str:
    return f"scope_{uuid.uuid4().hex[:24]}"


class ScopeRegistryRow(Base):
    """Typed scope identity; plugin rows are staged and never resolved as positive."""

    __tablename__ = "scope_registry"
    __table_args__ = (
        UniqueConstraint("scope_kind", "scope_id", name="uq_scope_registry_kind_id"),
        UniqueConstraint(
            "scope_kind",
            "scope_id",
            "tenant_id",
            "cabinet_id",
            "project_id",
            "plugin_instance_id",
            name="uq_scope_registry_typed_anchor",
        ),
        CheckConstraint(
            "scope_kind IN ('tenant', 'cabinet', 'project', 'plugin_instance')",
            name="ck_scope_registry_kind",
        ),
        CheckConstraint(
            "owner_state IN ('active', 'archived', 'quarantined')",
            name="ck_scope_registry_owner_state",
        ),
        CheckConstraint("source_kind <> ''", name="ck_scope_registry_source_kind_nonempty"),
        CheckConstraint("source_id <> ''", name="ck_scope_registry_source_id_nonempty"),
        CheckConstraint("schema_version <> ''", name="ck_scope_registry_schema_version_nonempty"),
        CheckConstraint("payload_digest <> ''", name="ck_scope_registry_payload_digest_nonempty"),
        CheckConstraint(
            "(scope_kind = 'tenant' AND scope_id = tenant_id "
            "AND cabinet_id IS NULL AND project_id IS NULL AND plugin_instance_id IS NULL) "
            "OR (scope_kind = 'cabinet' AND cabinet_id IS NOT NULL AND scope_id = cabinet_id "
            "AND tenant_id IS NOT NULL AND project_id IS NULL AND plugin_instance_id IS NULL) "
            "OR (scope_kind = 'project' AND project_id IS NOT NULL AND scope_id = project_id "
            "AND tenant_id IS NOT NULL AND cabinet_id IS NOT NULL AND plugin_instance_id IS NULL) "
            "OR (scope_kind = 'plugin_instance' AND plugin_instance_id IS NOT NULL "
            "AND scope_id = plugin_instance_id AND tenant_id IS NOT NULL "
            "AND (project_id IS NULL OR cabinet_id IS NOT NULL))",
            name="ck_scope_registry_typed_shape",
        ),
        CheckConstraint(
            "scope_kind <> 'plugin_instance' OR owner_state = 'quarantined'",
            name="ck_scope_registry_plugin_deferred",
        ),
        Index("ix_scope_registry_tenant_state", "tenant_id", "owner_state"),
        Index("ix_scope_registry_cabinet_project", "cabinet_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    scope_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(40), nullable=False)
    tenant_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("companies.id", name="fk_scope_registry_tenant", ondelete="RESTRICT"),
        nullable=False,
    )
    cabinet_id: Mapped[str | None] = mapped_column(
        String(40),
        ForeignKey("cabinet_instances.id", name="fk_scope_registry_cabinet", ondelete="RESTRICT"),
        nullable=True,
    )
    project_id: Mapped[str | None] = mapped_column(
        String(40),
        ForeignKey("projects.id", name="fk_scope_registry_project", ondelete="RESTRICT"),
        nullable=True,
    )
    # No FK yet: the reciprocal exact-instance key is owned by downstream M05.
    plugin_instance_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    owner_state: Mapped[str] = mapped_column(String(16), nullable=False, default="quarantined", server_default="quarantined")
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    checkpoint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ScopeOwnerMappingRow(Base):
    """Immutable source-identity mapping foundation; no writer is enabled here."""

    __tablename__ = "scope_owner_mappings"
    __table_args__ = (
        UniqueConstraint("source_kind", "source_id", "schema_version", name="uq_scope_owner_mapping_source"),
        CheckConstraint(
            "mapping_state IN ('mapped', 'archived', 'quarantined')",
            name="ck_scope_owner_mapping_state",
        ),
        CheckConstraint("source_kind <> ''", name="ck_scope_owner_mapping_source_kind_nonempty"),
        CheckConstraint("source_id <> ''", name="ck_scope_owner_mapping_source_id_nonempty"),
        CheckConstraint("schema_version <> ''", name="ck_scope_owner_mapping_schema_version_nonempty"),
        CheckConstraint(
            "source_payload_digest <> ''",
            name="ck_scope_owner_mapping_digest_nonempty",
        ),
        Index("ix_scope_owner_mapping_checkpoint", "checkpoint"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    source_payload_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_payload_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scope_registry_id: Mapped[str | None] = mapped_column(
        String(40),
        ForeignKey("scope_registry.id", name="fk_scope_owner_mapping_registry", ondelete="RESTRICT"),
        nullable=True,
    )
    mapping_state: Mapped[str] = mapped_column(String(16), nullable=False, default="quarantined", server_default="quarantined")
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checkpoint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ScopeOwnerQuarantineRow(Base):
    """Redacted unresolved source evidence; raw payloads and secrets are excluded."""

    __tablename__ = "scope_owner_quarantine"
    __table_args__ = (
        UniqueConstraint("source_kind", "source_id", "schema_version", "source_payload_digest", name="uq_scope_owner_quarantine_observation"),
        CheckConstraint("source_kind <> ''", name="ck_scope_owner_quarantine_source_kind_nonempty"),
        CheckConstraint("source_id <> ''", name="ck_scope_owner_quarantine_source_id_nonempty"),
        CheckConstraint("schema_version <> ''", name="ck_scope_owner_quarantine_schema_version_nonempty"),
        CheckConstraint("source_payload_digest <> ''", name="ck_scope_owner_quarantine_digest_nonempty"),
        CheckConstraint("reason_code <> ''", name="ck_scope_owner_quarantine_reason_nonempty"),
        Index("ix_scope_owner_quarantine_checkpoint", "checkpoint"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    source_payload_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    resolved_tenant_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resolved_cabinet_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resolved_project_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    checkpoint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ScopeOwnerAuditRow(Base):
    """Append-shaped redacted migration evidence; mutation privileges are a DDL gate."""

    __tablename__ = "scope_owner_audit"
    __table_args__ = (
        CheckConstraint("source_kind <> ''", name="ck_scope_owner_audit_source_kind_nonempty"),
        CheckConstraint("source_id <> ''", name="ck_scope_owner_audit_source_id_nonempty"),
        CheckConstraint("schema_version <> ''", name="ck_scope_owner_audit_schema_version_nonempty"),
        CheckConstraint("source_payload_digest <> ''", name="ck_scope_owner_audit_digest_nonempty"),
        CheckConstraint("event_kind <> ''", name="ck_scope_owner_audit_event_nonempty"),
        CheckConstraint("reason_code <> ''", name="ck_scope_owner_audit_reason_nonempty"),
        CheckConstraint("correlation_id <> ''", name="ck_scope_owner_audit_correlation_nonempty"),
        Index("ix_scope_owner_audit_source", "source_kind", "source_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    source_payload_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    event_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    checkpoint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
