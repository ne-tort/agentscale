"""Canonical typed owner/scope registry foundation for migration evidence."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base
from prodavan.infrastructure.persistence.models.projects import ProjectRow


CANONICAL_TENANT_TABLE = "companies"
CANONICAL_TENANT_ID_LENGTH = 40
SCOPE_ID_LENGTH = 40
SCOPE_KINDS = ("tenant", "cabinet", "project", "plugin_instance")
OWNER_STATES = ("active", "archived", "quarantined")

CanonicalTenantId = Annotated[str, "companies.id canonical tenant key"]


def _install_parent_compatibility_keys() -> None:
    """Expose the stable project tuple used by the typed project anchor."""
    project_key = "uq_projects_scope_owner_parent_tuple"
    if not any(constraint.name == project_key for constraint in ProjectRow.__table__.constraints):
        ProjectRow.__table__.append_constraint(
            UniqueConstraint("id", "company_id", "cabinet_id", name=project_key)
        )


_install_parent_compatibility_keys()


def _id() -> str:
    return f"scope_{uuid.uuid4().hex[:24]}"


class CanonicalTenantAnchorRow(Base):
    """Typed retained identity alias over the existing ``companies.id`` key."""

    __tablename__ = "scope_owner_tenant_anchors"
    __table_args__ = (
        UniqueConstraint("tenant_id", "tenant_type", name="uq_scope_owner_tenant_identity"),
        UniqueConstraint(
            "tenant_id", "tenant_type", "owner_state",
            name="uq_scope_owner_tenant_identity_state",
        ),
        CheckConstraint("tenant_type = 'company'", name="ck_scope_owner_tenant_type"),
        CheckConstraint(
            "owner_state IN ('active', 'archived', 'quarantined')",
            name="ck_scope_owner_tenant_state",
        ),
        CheckConstraint(
            "tenant_id <> '' AND tenant_id = btrim(tenant_id) "
            "AND tenant_id !~ '[^A-Za-z0-9_.:-]'",
            name="ck_scope_owner_tenant_id_well_formed",
        ),
        CheckConstraint("source_kind <> ''", name="ck_scope_owner_tenant_source_kind"),
        CheckConstraint("source_id <> ''", name="ck_scope_owner_tenant_source_id"),
        CheckConstraint("schema_version <> ''", name="ck_scope_owner_tenant_schema"),
        CheckConstraint("payload_digest <> ''", name="ck_scope_owner_tenant_digest"),
        ForeignKeyConstraint(
            ["tenant_id"], ["companies.id"],
            name="fk_scope_owner_tenant_company",
            ondelete="RESTRICT",
        ),
        Index("ix_scope_owner_tenant_state", "tenant_id", "owner_state"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    tenant_id: Mapped[CanonicalTenantId] = mapped_column(String(CANONICAL_TENANT_ID_LENGTH), nullable=False)
    tenant_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="company", server_default="company"
    )
    owner_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="quarantined", server_default="quarantined"
    )
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ScopeOwnerCabinetAnchorRow(Base):
    """Cabinet and canonical tenant tuple retained as non-destructive evidence."""

    __tablename__ = "scope_owner_cabinet_anchors"
    __table_args__ = (
        UniqueConstraint(
            "cabinet_id", "tenant_id", "tenant_type",
            name="uq_scope_owner_cabinet_anchor_typed_tuple",
        ),
        UniqueConstraint(
            "cabinet_id", "tenant_id", "tenant_type", "owner_state",
            name="uq_scope_owner_cabinet_anchor_typed_tuple_state",
        ),
        CheckConstraint("tenant_type = 'company'", name="ck_scope_owner_cabinet_anchor_tenant_type"),
        CheckConstraint(
            "owner_state IN ('active', 'archived', 'quarantined')",
            name="ck_scope_owner_cabinet_anchor_state",
        ),
        CheckConstraint(
            "cabinet_id <> '' AND tenant_id <> ''",
            name="ck_scope_owner_cabinet_anchor_ids_nonempty",
        ),
        CheckConstraint("source_kind <> ''", name="ck_scope_owner_cabinet_anchor_source_kind"),
        CheckConstraint("source_id <> ''", name="ck_scope_owner_cabinet_anchor_source_id"),
        CheckConstraint("schema_version <> ''", name="ck_scope_owner_cabinet_anchor_schema"),
        CheckConstraint("payload_digest <> ''", name="ck_scope_owner_cabinet_anchor_digest"),
        ForeignKeyConstraint(
            ["cabinet_id", "tenant_id"],
            ["cabinet_company_grants.cabinet_id", "cabinet_company_grants.company_id"],
            name="fk_scope_owner_cabinet_anchor_company_grant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "tenant_type"],
            ["scope_owner_tenant_anchors.tenant_id", "scope_owner_tenant_anchors.tenant_type"],
            name="fk_scope_owner_cabinet_anchor_typed_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["companies.id"],
            name="fk_scope_owner_cabinet_anchor_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["cabinet_id"], ["cabinet_instances.id"],
            name="fk_scope_owner_cabinet_anchor_cabinet",
            ondelete="RESTRICT",
        ),
        Index("ix_scope_owner_cabinet_anchor_tenant_state", "tenant_id", "owner_state"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    cabinet_id: Mapped[str] = mapped_column(String(SCOPE_ID_LENGTH), nullable=False)
    tenant_id: Mapped[CanonicalTenantId] = mapped_column(
        String(CANONICAL_TENANT_ID_LENGTH), nullable=False
    )
    tenant_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="company", server_default="company"
    )
    owner_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="quarantined", server_default="quarantined"
    )
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ScopeOwnerProjectAnchorRow(Base):
    """Explicit project/tenant/cabinet compatibility evidence.

    The typed tuple is persisted independently so a mismatch cannot be hidden
    by three unrelated parent FKs. Project and cabinet grant lifecycle remains
    mutable and must be rechecked by the trusted transaction before activation.
    """

    __tablename__ = "scope_owner_project_anchors"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "tenant_id", "tenant_type", "cabinet_id", "project_status", "owner_state",
            name="uq_scope_owner_project_anchor_typed_tuple_state",
        ),
        CheckConstraint("tenant_type = 'company'", name="ck_scope_owner_project_anchor_tenant_type"),
        CheckConstraint(
            "project_status IN ('draft', 'active', 'paused', 'error', 'completed', 'deleted')",
            name="ck_scope_owner_project_anchor_project_status",
        ),
        CheckConstraint(
            "owner_state IN ('active', 'archived', 'quarantined')",
            name="ck_scope_owner_project_anchor_state",
        ),
        CheckConstraint("project_id <> '' AND tenant_id <> '' AND cabinet_id <> ''", name="ck_scope_owner_project_anchor_ids_nonempty"),
        CheckConstraint("source_kind <> ''", name="ck_scope_owner_project_anchor_source_kind"),
        CheckConstraint("source_id <> ''", name="ck_scope_owner_project_anchor_source_id"),
        CheckConstraint("schema_version <> ''", name="ck_scope_owner_project_anchor_schema"),
        CheckConstraint("payload_digest <> ''", name="ck_scope_owner_project_anchor_digest"),
        ForeignKeyConstraint(
            ["project_id", "tenant_id", "cabinet_id"],
            ["projects.id", "projects.company_id", "projects.cabinet_id"],
            name="fk_scope_owner_project_anchor_project_parent",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "tenant_type"],
            ["scope_owner_tenant_anchors.tenant_id", "scope_owner_tenant_anchors.tenant_type"],
            name="fk_scope_owner_project_anchor_typed_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["companies.id"],
            name="fk_scope_owner_project_anchor_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["cabinet_id"], ["cabinet_instances.id"],
            name="fk_scope_owner_project_anchor_cabinet",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["cabinet_id", "tenant_id", "tenant_type"],
            [
                "scope_owner_cabinet_anchors.cabinet_id",
                "scope_owner_cabinet_anchors.tenant_id",
                "scope_owner_cabinet_anchors.tenant_type",
            ],
            name="fk_scope_owner_project_anchor_cabinet_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["cabinet_id", "tenant_id"],
            ["cabinet_company_grants.cabinet_id", "cabinet_company_grants.company_id"],
            name="fk_scope_owner_project_anchor_cabinet_company_grant",
            ondelete="RESTRICT",
        ),
        Index("ix_scope_owner_project_anchor_tenant_cabinet", "tenant_id", "cabinet_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(String(40), nullable=False)
    tenant_id: Mapped[CanonicalTenantId] = mapped_column(String(40), nullable=False)
    tenant_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="company", server_default="company"
    )
    cabinet_id: Mapped[str] = mapped_column(String(40), nullable=False)
    project_status: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="quarantined", server_default="quarantined"
    )
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ScopeRegistryRow(Base):
    """Typed scope identity; plugin rows are staged and never resolved as positive."""

    __tablename__ = "scope_registry"
    __table_args__ = (
        UniqueConstraint("scope_kind", "scope_id", name="uq_scope_registry_kind_id"),
        UniqueConstraint(
            "scope_kind",
            "scope_id",
            "tenant_id",
            "tenant_type",
            "cabinet_id",
            "project_id",
            "project_status",
            "plugin_instance_id",
            name="uq_scope_registry_typed_anchor",
        ),
        CheckConstraint(
            "scope_kind IN ('tenant', 'cabinet', 'project', 'plugin_instance')",
            name="ck_scope_registry_kind",
        ),
        CheckConstraint(
            "tenant_type IN ('company')",
            name="ck_scope_registry_tenant_type",
        ),
        CheckConstraint(
            "owner_state IN ('active', 'archived', 'quarantined')",
            name="ck_scope_registry_owner_state",
        ),
        CheckConstraint(
            "scope_id <> '' AND scope_id = btrim(scope_id) "
            "AND scope_id !~ '[^A-Za-z0-9_.:-]'",
            name="ck_scope_registry_scope_id_well_formed",
        ),
        CheckConstraint(
            "tenant_id <> '' AND tenant_id = btrim(tenant_id) "
            "AND tenant_id !~ '[^A-Za-z0-9_.:-]'",
            name="ck_scope_registry_tenant_id_well_formed",
        ),
        CheckConstraint("source_kind <> ''", name="ck_scope_registry_source_kind_nonempty"),
        CheckConstraint("source_id <> ''", name="ck_scope_registry_source_id_nonempty"),
        CheckConstraint("schema_version <> ''", name="ck_scope_registry_schema_version_nonempty"),
        CheckConstraint("payload_digest <> ''", name="ck_scope_registry_payload_digest_nonempty"),
        CheckConstraint(
            "(scope_kind = 'tenant' AND scope_id = tenant_id "
            "AND cabinet_id IS NULL AND project_id IS NULL AND project_status IS NULL "
            "AND plugin_instance_id IS NULL) "
            "OR (scope_kind = 'cabinet' AND cabinet_id IS NOT NULL AND scope_id = cabinet_id "
            "AND project_id IS NULL AND project_status IS NULL AND plugin_instance_id IS NULL) "
            "OR (scope_kind = 'project' AND project_id IS NOT NULL AND scope_id = project_id "
            "AND cabinet_id IS NOT NULL AND project_status IN ('draft', 'active', 'paused', 'error', 'completed', 'deleted') "
            "AND plugin_instance_id IS NULL) "
            "OR (scope_kind = 'plugin_instance' AND plugin_instance_id IS NOT NULL "
            "AND scope_id = plugin_instance_id AND tenant_id IS NOT NULL "
            "AND (project_id IS NULL OR (cabinet_id IS NOT NULL AND project_status IS NOT NULL)))",
            name="ck_scope_registry_typed_shape",
        ),
        CheckConstraint(
            "scope_kind <> 'plugin_instance' OR owner_state = 'quarantined'",
            name="ck_scope_registry_plugin_deferred",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "tenant_type", "owner_state"],
            [
                "scope_owner_tenant_anchors.tenant_id",
                "scope_owner_tenant_anchors.tenant_type",
                "scope_owner_tenant_anchors.owner_state",
            ],
            name="fk_scope_registry_tenant_owner_state",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["cabinet_id", "tenant_id", "tenant_type", "owner_state"],
            [
                "scope_owner_cabinet_anchors.cabinet_id",
                "scope_owner_cabinet_anchors.tenant_id",
                "scope_owner_cabinet_anchors.tenant_type",
                "scope_owner_cabinet_anchors.owner_state",
            ],
            name="fk_scope_registry_cabinet_typed_parent",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "tenant_id", "tenant_type", "cabinet_id", "project_status", "owner_state"],
            [
                "scope_owner_project_anchors.project_id",
                "scope_owner_project_anchors.tenant_id",
                "scope_owner_project_anchors.tenant_type",
                "scope_owner_project_anchors.cabinet_id",
                "scope_owner_project_anchors.project_status",
                "scope_owner_project_anchors.owner_state",
            ],
            name="fk_scope_registry_project_typed_parent",
            ondelete="RESTRICT",
        ),
        Index("ix_scope_registry_tenant_state", "tenant_id", "owner_state"),
        Index("ix_scope_registry_cabinet_project", "cabinet_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id)
    scope_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(40), nullable=False)
    tenant_id: Mapped[CanonicalTenantId] = mapped_column(
        String(CANONICAL_TENANT_ID_LENGTH), nullable=False
    )
    tenant_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="company", server_default="company"
    )
    cabinet_id: Mapped[str | None] = mapped_column(String(SCOPE_ID_LENGTH), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(SCOPE_ID_LENGTH), nullable=True)
    project_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
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
