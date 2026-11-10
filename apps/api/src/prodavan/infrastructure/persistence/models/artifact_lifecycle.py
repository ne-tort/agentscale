"""Durable append-only lifecycle journal for plugin artifact publication.

The writer appends a row in the same ``AsyncSession`` and unit of work as the
artifact publication/promotion, before the outer owner's single commit. Rows
are never corrected in place: a correction is a new lifecycle entry. This
schema deliberately installs no mutation triggers or ORM mutation listeners.

``detail`` is a short, redacted human-readable summary. It must not contain
credentials, secret values, authorization headers, request/response payloads,
or other raw sensitive material. ``operation_token`` is an opaque idempotency
and operation-scoped compensation identifier, not an authentication token.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


def _lifecycle_id() -> str:
    return f"pal_{uuid.uuid4().hex}"


class PluginArtifactLifecycleRow(Base):
    """One immutable publish/promote/rollback fact bound to one artifact."""

    __tablename__ = "plugin_artifact_lifecycle"
    __table_args__ = (
        UniqueConstraint(
            "artifact_id",
            "operation_token",
            name="uq_plugin_artifact_lifecycle_artifact_operation_token",
        ),
        ForeignKeyConstraint(
            ["artifact_id"],
            ["plugin_artifacts.id"],
            name="fk_plugin_artifact_lifecycle_artifact",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "operation IN ('publish', 'promote', 'rollback')",
            name="ck_plugin_artifact_lifecycle_operation",
        ),
        CheckConstraint(
            "btrim(actor) <> ''",
            name="ck_plugin_artifact_lifecycle_actor_nonempty",
        ),
        CheckConstraint(
            "btrim(operation_token) <> ''",
            name="ck_plugin_artifact_lifecycle_operation_token_nonempty",
        ),
        Index(
            "ix_plugin_artifact_lifecycle_artifact_occurred_at",
            "artifact_id",
            "occurred_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(80), primary_key=True, default=_lifecycle_id
    )
    artifact_id: Mapped[str] = mapped_column(String(80), nullable=False)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    actor: Mapped[str] = mapped_column(String(160), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    operation_token: Mapped[str] = mapped_column(String(128), nullable=False)
    # SHA-256 of the complete normalized RollbackCommand; reason is never stored.
    command_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)


__all__ = ["PluginArtifactLifecycleRow"]
