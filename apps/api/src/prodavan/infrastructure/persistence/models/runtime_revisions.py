"""ORM rows for immutable project runtime revisions and materialization."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class CapabilityBundleRow(Base):
    """Content-addressed capability manifest belonging to one revision."""

    __tablename__ = "capability_bundles"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    digest: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectRuntimeRevisionRow(Base):
    """Durable lifecycle state for an immutable project runtime snapshot."""

    __tablename__ = "project_runtime_revisions"
    __table_args__ = (
        UniqueConstraint("project_id", "revision", name="uq_project_runtime_revision_number"),
        CheckConstraint(
            "state IN ('planned', 'validated', 'applying', 'ready', 'failed', 'superseded')",
            name="ck_project_runtime_revision_state",
        ),
        Index(
            "ix_project_runtime_revisions_project_state_revision",
            "project_id",
            "state",
            text("revision DESC"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _id("rr"))
    project_id: Mapped[str] = mapped_column(String(40), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    bundle_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("capability_bundles.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="planned", server_default="planned")
    applied_revision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pod_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pod_uid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    hydrate_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    bridge_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    runtime_image_digest: Mapped[str | None] = mapped_column(String(256), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    diagnostics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_event: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MaterializationEntryRow(Base):
    """Observable generated workspace path and its source provenance."""

    __tablename__ = "materialization_entries"
    __table_args__ = (
        UniqueConstraint("revision_id", "workspace_path", name="uq_materialization_revision_path"),
        UniqueConstraint("revision_id", "idempotency_key", name="uq_materialization_revision_idempotency"),
        CheckConstraint(
            "status IN ('planned', 'staged', 'applied', 'failed')",
            name="ck_materialization_entry_status",
        ),
        CheckConstraint("generation >= 0", name="ck_materialization_entry_generation_nonnegative"),
        Index("ix_materialization_entries_revision_status", "revision_id", "status"),
        Index("ix_materialization_entries_revision_generation", "revision_id", "generation"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _id("me"))
    revision_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("project_runtime_revisions.id", ondelete="CASCADE"), nullable=False
    )
    workspace_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    operation_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planned", server_default="planned")
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_asset_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_capability_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provenance: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
