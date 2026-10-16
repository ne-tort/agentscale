"""External Chat API + outgoing webhooks — ORM.

Two concerns, one file (they ship together as the external surface):

- `api_app_keys` — machine credentials for the external chat API
  (`/api/v1/external/...`). A key binds an employee (auth-as that cabinet
  member) + company (+ optional cabinet scope) and carries scopes. The secret
  itself is only ever returned once at creation; the row stores a sha256 hash.
- `webhook_subscriptions` + `webhook_deliveries` — outgoing webhooks
  (ChatGPT-style): a subscription points at a URL with an HMAC secret
  (secret_ref, not inline); deliveries are the transactional outbox rows a
  worker delivers with retries + exponential backoff.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.infrastructure.persistence.models.base import Base


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class ApiAppKeyRow(Base):
    """External API credential: machine key acting as a cabinet employee."""

    __tablename__ = "api_app_keys"
    __table_args__ = (
        Index("ix_api_app_keys_company", "company_id"),
        Index("ix_api_app_keys_prefix", "token_prefix"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("ak"))
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    # None = key may touch every cabinet the employee belongs to
    cabinet_id: Mapped[str | None] = mapped_column(
        ForeignKey("cabinet_instances.id", ondelete="CASCADE"), nullable=True
    )
    # auth-as: the key acts as this cabinet member
    employee_id: Mapped[str] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    # identification without the secret: first chars of the token
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    # sha256 hex of the secret (the secret is shown once at creation, never stored)
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scopes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WebhookSubscriptionRow(Base):
    """Outgoing webhook subscription (ChatGPT-style) for chat events."""

    __tablename__ = "webhook_subscriptions"
    __table_args__ = (
        Index("ix_webhook_subs_company", "company_id"),
        Index("ix_webhook_subs_project", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("whk"))
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    # None = every project of the company
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    # HMAC secret lives in the secret store (never inline in the row)
    secret_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    events: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    fail_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WebhookDeliveryRow(Base):
    """Outbox row: one event → one subscription. Claimable + retried."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index("ix_webhook_del_pending", "status", "next_attempt_at"),
        Index("ix_webhook_del_sub", "subscription_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("whd"))
    subscription_id: Mapped[str] = mapped_column(
        ForeignKey("webhook_subscriptions.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # lease for the delivery worker (SKIP LOCKED pattern)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    leased_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
