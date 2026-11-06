"""ORM models for unified prompt, file, and MCP resources.

The resource root is stable, versions are append-only, and bindings carry the
scope and inheritance policy explicitly. This model is intentionally separate
from runtime revision tables and can be introduced by a later migration.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class ResourceRow(Base):
    __tablename__ = "resources"
    __table_args__ = (
        UniqueConstraint("scope_kind", "scope_id", "kind", "slug", name="uq_resource_scope_kind_slug"),
        Index("ix_resources_scope", "scope_kind", "scope_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("res"))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str] = mapped_column(String(260), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(80), nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default="{}")
    active_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ResourceVersionRow(Base):
    __tablename__ = "resource_versions"
    __table_args__ = (
        UniqueConstraint("resource_id", "number", name="uq_resource_version_number"),
        Index("ix_resource_versions_resource_status", "resource_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("rver"))
    resource_id: Mapped[str] = mapped_column(String(40), ForeignKey("resources.id", ondelete="CASCADE"), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResourceBindingRow(Base):
    __tablename__ = "resource_bindings"
    __table_args__ = (
        UniqueConstraint(
            "resource_id",
            "target_scope_kind",
            "target_scope_id",
            "session_id",
            name="uq_resource_binding_target",
        ),
        Index("ix_resource_bindings_target", "target_scope_kind", "target_scope_id", "session_id"),
        Index(
            "uq_resource_binding_target_without_session",
            "resource_id",
            "target_scope_kind",
            "target_scope_id",
            unique=True,
            postgresql_where=text("session_id IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("rbind"))
    resource_id: Mapped[str] = mapped_column(String(40), ForeignKey("resources.id", ondelete="CASCADE"), nullable=False)
    target_scope_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target_scope_id: Mapped[str] = mapped_column(String(80), nullable=False)
    binding_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="global", server_default="global")
    child_may_edit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    project_ids: Mapped[list[str]] = mapped_column(ARRAY(String(80)), nullable=False, default=list, server_default="{}")
    session_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
