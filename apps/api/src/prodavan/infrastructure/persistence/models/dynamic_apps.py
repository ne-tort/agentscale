"""SQLAlchemy rows for dynamic declarative apps and isolated plugins."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


_LIFECYCLE_CHECK = "('draft', 'published', 'disabled', 'revoked')"
_SCOPE_CHECK = "('tenant', 'cabinet', 'project')"


class AppDefinitionRow(Base):
    """Stable app identity; versions are append-only snapshots below it."""

    __tablename__ = "dynamic_app_definitions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "app_id", name="uq_dynamic_app_definition_tenant_app"),
        ForeignKeyConstraint(
            ["current_version_id", "id"],
            ["dynamic_app_versions.id", "dynamic_app_versions.app_definition_id"],
            name="fk_dynamic_app_definition_current_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"status IN {_LIFECYCLE_CHECK}", name="ck_dynamic_app_definition_status"),
        CheckConstraint("status <> 'published' OR current_version_id IS NOT NULL", name="ck_dynamic_app_definition_published_version"),
        CheckConstraint(f"scope_kind IN {_SCOPE_CHECK}", name="ck_dynamic_app_definition_scope_kind"),
        Index("ix_dynamic_app_definitions_scope", "tenant_id", "scope_kind", "scope_id"),
        Index("ix_dynamic_app_definitions_status", "tenant_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=lambda: _id("app"))
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    app_id: Mapped[str] = mapped_column(String(160), nullable=False)
    display_name: Mapped[str] = mapped_column(String(260), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
    current_version_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AppVersionRow(Base):
    """Immutable manifest/artifact snapshot for an app definition."""

    __tablename__ = "dynamic_app_versions"
    __table_args__ = (
        UniqueConstraint("app_definition_id", "manifest_version", name="uq_dynamic_app_version_manifest_version"),
        UniqueConstraint("app_definition_id", "artifact_digest", name="uq_dynamic_app_version_artifact_digest"),
        UniqueConstraint("id", "app_definition_id", name="uq_dynamic_app_version_id_definition"),
        CheckConstraint(f"status IN {_LIFECYCLE_CHECK}", name="ck_dynamic_app_version_status"),
        CheckConstraint("artifact_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_dynamic_app_version_digest_sha256"),
        CheckConstraint("manifest_version <> ''", name="ck_dynamic_app_version_manifest_version_nonempty"),
        CheckConstraint("manifest->>'protocol' = '1.0'", name="ck_dynamic_app_version_manifest_protocol"),
        CheckConstraint("manifest ? 'appId' AND manifest ? 'version' AND manifest ? 'displayName' AND manifest ? 'entrypoints' AND manifest ? 'requestedCapabilities'", name="ck_dynamic_app_version_manifest_v1_fields"),
        ForeignKeyConstraint(
            ["rollback_from_version_id", "app_definition_id"],
            ["dynamic_app_versions.id", "dynamic_app_versions.app_definition_id"],
            name="fk_dynamic_app_version_rollback_from",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["rollback_to_version_id", "app_definition_id"],
            ["dynamic_app_versions.id", "dynamic_app_versions.app_definition_id"],
            name="fk_dynamic_app_version_rollback_to",
            ondelete="RESTRICT",
        ),
        CheckConstraint("(rollback_from_version_id IS NULL) = (rollback_to_version_id IS NULL)", name="ck_dynamic_app_version_rollback_pair"),
        Index("ix_dynamic_app_versions_app_status", "app_definition_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=lambda: _id("aver"))
    app_definition_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("dynamic_app_definitions.id", ondelete="CASCADE"), nullable=False
    )
    manifest_version: Mapped[str] = mapped_column(String(128), nullable=False)
    artifact_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    artifact_uri: Mapped[str | None] = mapped_column(String(2_048), nullable=True)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    requested_capabilities: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
    rollback_from_version_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    rollback_to_version_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    rollback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    rollback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AppGrantRow(Base):
    """Capability decision set for one immutable app version and scope."""

    __tablename__ = "dynamic_app_grants"
    __table_args__ = (
        UniqueConstraint("app_version_id", "scope_kind", "scope_id", name="uq_dynamic_app_grant_version_scope"),
        ForeignKeyConstraint(
            ["app_version_id", "app_definition_id"],
            ["dynamic_app_versions.id", "dynamic_app_versions.app_definition_id"],
            name="fk_dynamic_app_grant_version_definition",
            ondelete="CASCADE",
        ),
        CheckConstraint(f"scope_kind IN {_SCOPE_CHECK}", name="ck_dynamic_app_grant_scope_kind"),
        CheckConstraint("expires_at IS NULL OR expires_at > issued_at", name="ck_dynamic_app_grant_expiry_after_issue"),
        Index("ix_dynamic_app_grants_scope", "scope_kind", "scope_id"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=lambda: _id("agr"))
    app_definition_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("dynamic_app_definitions.id", ondelete="CASCADE"), nullable=False
    )
    app_version_id: Mapped[str] = mapped_column(String(80), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(80), nullable=False)
    grants: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AppInstanceRow(Base):
    """A scoped installation, retaining deployment and rollback provenance."""

    __tablename__ = "dynamic_app_instances"
    __table_args__ = (
        UniqueConstraint("app_definition_id", "scope_kind", "scope_id", name="uq_dynamic_app_instance_scope"),
        ForeignKeyConstraint(
            ["app_version_id", "app_definition_id"],
            ["dynamic_app_versions.id", "dynamic_app_versions.app_definition_id"],
            name="fk_dynamic_app_instance_version_definition",
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"scope_kind IN {_SCOPE_CHECK}", name="ck_dynamic_app_instance_scope_kind"),
        CheckConstraint(f"status IN {_LIFECYCLE_CHECK}", name="ck_dynamic_app_instance_status"),
        Index("ix_dynamic_app_instances_scope_status", "scope_kind", "scope_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=lambda: _id("ains"))
    app_definition_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("dynamic_app_definitions.id", ondelete="CASCADE"), nullable=False
    )
    app_version_id: Mapped[str] = mapped_column(String(80), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
    runtime_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    rollback_from_version_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    rollback_to_version_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    rollback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    rollback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


__all__ = ["AppDefinitionRow", "AppGrantRow", "AppInstanceRow", "AppVersionRow"]
