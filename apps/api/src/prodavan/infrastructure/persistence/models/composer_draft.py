"""Employee composer drafts for chat input (per session or project-pending)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def composer_draft_scope_key(*, session_id: str | None, project_id: str) -> str:
    """Stable unique key: session draft or project-pending new chat."""
    if session_id:
        return f"s:{session_id}"
    return f"p:{project_id}"


class EmployeeComposerDraftRow(Base):
    """Cached composer text — durable across navigation; not part of chat transcript.

    - ``session_id`` set → draft for an existing chat
    - ``session_id`` null → pending «Новый диалог» for ``project_id``
    """

    __tablename__ = "employee_composer_drafts"
    __table_args__ = (
        UniqueConstraint(
            "employee_id",
            "scope_key",
            name="uq_composer_draft_employee_scope",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("acd"))
    employee_id: Mapped[str] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    scope_key: Mapped[str] = mapped_column(String(80), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
