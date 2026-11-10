"""Durable administrative pre-approval for plugin CI publication.

This table deliberately has no ACTIVE/REVOKED grant status and no FK to an
artifact row: approval must be possible before the first artifact is PUBLISHED.
The bundle digest and full policy are immutable exact-binding fields. Only
consumed_at changes, after the subsequent successful admin grant saga.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base
from prodavan.infrastructure.persistence.models.plugin_platform import ARTIFACT_DIGEST_SQL


def _approval_id() -> str:
    return f"ppr_{uuid.uuid4().hex}"


class PluginPublicationApprovalRow(Base):
    """Exact company/plugin/version/digest + capabilities/egress/quotas decision."""

    __tablename__ = "plugin_publication_approvals"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "plugin_id",
            "manifest_version",
            "artifact_digest",
            name="uq_plugin_publication_approval_binding",
        ),
        CheckConstraint(
            f"artifact_digest ~ '{ARTIFACT_DIGEST_SQL}'",
            name="ck_plugin_publication_approval_digest",
        ),
        CheckConstraint(
            "jsonb_typeof(capabilities) = 'array' AND jsonb_array_length(capabilities) > 0",
            name="ck_plugin_publication_approval_capabilities",
        ),
        CheckConstraint(
            "jsonb_typeof(egress_policy) = 'object'",
            name="ck_plugin_publication_approval_egress_object",
        ),
        CheckConstraint(
            "jsonb_typeof(quota) = 'object'",
            name="ck_plugin_publication_approval_quota_object",
        ),
        CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= created_at",
            name="ck_plugin_publication_approval_consumed_after_created",
        ),
        Index(
            "ix_plugin_publication_approvals_company_plugin",
            "company_id",
            "plugin_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=_approval_id)
    company_id: Mapped[str] = mapped_column(String(40), nullable=False)
    plugin_id: Mapped[str] = mapped_column(String(160), nullable=False)
    manifest_version: Mapped[str] = mapped_column(String(128), nullable=False)
    artifact_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    capabilities: Mapped[list] = mapped_column(JSONB, nullable=False)
    egress_policy: Mapped[dict] = mapped_column(JSONB, nullable=False)
    quota: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = ["PluginPublicationApprovalRow"]
