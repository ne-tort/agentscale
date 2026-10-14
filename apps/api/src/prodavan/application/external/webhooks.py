"""Outgoing webhooks (ChatGPT-style): subscriptions + event fanout + delivery.

A subscription points at a URL with an HMAC secret (secret store, never inline).
On a covered event (chat turn completed, …) one outbox row per matching
subscription is created (webhook_deliveries); a worker claims rows
(SKIP LOCKED + lease), POSTs the signed body, retries with backoff, and
auto-disables a subscription after repeated failures.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.config.settings import settings
from prodavan.domain.errors import AppError
from prodavan.domain.projects.webhook_hmac import webhook_signature
from prodavan.infrastructure.persistence.models.external_api import (
    WebhookDeliveryRow,
    WebhookSubscriptionRow,
)
from prodavan.infrastructure.persistence.models.projects import ProjectRow
from prodavan.infrastructure.secrets.store import get_secret_store

logger = logging.getLogger(__name__)

EVENT_CHAT_MESSAGE_COMPLETED = "chat.message.completed"
EVENT_CHAT_TURN_FAILED = "chat.turn.failed"
SUPPORTED_EVENTS = frozenset({EVENT_CHAT_MESSAGE_COMPLETED, EVENT_CHAT_TURN_FAILED})

# Retry: 5 attempts, backoff 1m/5m/30m/2h, then the delivery is exhausted.
_BACKOFF = [60, 300, 1800, 7200]
_MAX_ATTEMPTS = len(_BACKOFF) + 1
# subscription auto-disables after this many consecutive delivery failures
_AUTO_DISABLE_AFTER = 10
_DELIVERY_TIMEOUT_S = 15
_LEASE_S = 120


class WebhookService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------ подписки

    async def subscribe(
        self,
        *,
        company_id: str,
        url: str,
        events: list[str],
        project_id: str | None = None,
        created_by: str | None = None,
    ) -> tuple[WebhookSubscriptionRow, str]:
        """Create a subscription; returns (row, secret). Secret shown once."""
        url = url.strip()
        if not url.startswith(("https://", "http://")):
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="url must be http(s)",
            )
        bad = [e for e in events if e not in SUPPORTED_EVENTS]
        if not events or bad:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"events must be non-empty subset of {sorted(SUPPORTED_EVENTS)}",
            )
        if project_id is not None:
            project = await self._session.get(ProjectRow, project_id)
            if project is None or project.company_id != company_id:
                raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="project not found")
        import secrets as _secrets

        secret = _secrets.token_urlsafe(32)
        secret_ref = get_secret_store().put(f"webhook/{company_id}", secret)
        row = WebhookSubscriptionRow(
            company_id=company_id,
            project_id=project_id or None,
            url=url,
            secret_ref=secret_ref,
            events=list(events),
            enabled=True,
            created_by=created_by,
        )
        self._session.add(row)
        await self._session.flush()
        return row, secret

    async def list_for_company(
        self, company_id: str, *, project_id: str | None = None
    ) -> list[WebhookSubscriptionRow]:
        q = select(WebhookSubscriptionRow).where(
            WebhookSubscriptionRow.company_id == company_id
        )
        if project_id:
            q = q.where(WebhookSubscriptionRow.project_id == project_id)
        q = q.order_by(WebhookSubscriptionRow.created_at.desc())
        result = await self._session.execute(q)
        return list(result.scalars().all())

    async def delete(self, subscription_id: str, *, company_id: str) -> bool:
        q = await self._session.execute(
            select(WebhookSubscriptionRow).where(
                WebhookSubscriptionRow.id == subscription_id,
                WebhookSubscriptionRow.company_id == company_id,
            )
        )
        row = q.scalars().first()
        if row is None:
            return False
        if row.secret_ref:
            try:
                get_secret_store().delete(row.secret_ref)
            except Exception:
                logger.warning("webhook secret delete failed ref=%s", row.secret_ref)
        await self._session.delete(row)
        await self._session.flush()
        return True

    # ------------------------------------------------------------ fanout

    async def emit(
        self,
        *,
        company_id: str,
        project_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> int:
        """Enqueue one delivery per matching enabled subscription."""
        if event_type not in SUPPORTED_EVENTS:
            return 0
        q = await self._session.execute(
            select(WebhookSubscriptionRow).where(
                WebhookSubscriptionRow.company_id == company_id,
                WebhookSubscriptionRow.enabled.is_(True),
            )
        )
        subs = [
            s
            for s in q.scalars().all()
            if (s.project_id is None or s.project_id == project_id)
            and event_type in (s.events or [])
        ]
        now = datetime.now(UTC)
        for sub in subs:
            self._session.add(
                WebhookDeliveryRow(
                    subscription_id=sub.id,
                    event_type=event_type,
                    payload_json=payload,
                    status="pending",
                    next_attempt_at=now,
                )
            )
        if subs:
            await self._session.flush()
        return len(subs)

    # ------------------------------------------------------------ доставка

    async def deliver_pending(self, *, limit: int = 50) -> dict[str, int]:
        """Claim pending deliveries and POST them (worker entry)."""
        now = datetime.now(UTC)
        q = await self._session.execute(
            select(WebhookDeliveryRow)
            .where(
                WebhookDeliveryRow.status == "pending",
                WebhookDeliveryRow.next_attempt_at <= now,
            )
            .order_by(WebhookDeliveryRow.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = list(q.scalars().all())
        if not rows:
            return {"claimed": 0, "delivered": 0, "failed": 0}
        leased_until = now + timedelta(seconds=_LEASE_S)
        for r in rows:
            r.leased_by = "worker"
            r.lease_until = leased_until
        await self._session.flush()

        stats = {"claimed": len(rows), "delivered": 0, "failed": 0}
        for row in rows:
            ok = await self._deliver_one(row)
            if ok:
                stats["delivered"] += 1
            else:
                stats["failed"] += 1
        await self._session.commit()
        return stats

    async def _deliver_one(self, row: WebhookDeliveryRow) -> bool:
        sub = await self._session.get(WebhookSubscriptionRow, row.subscription_id)
        if sub is None or not sub.enabled:
            row.status = "exhausted"
            row.last_error = "subscription removed or disabled"
            return False
        try:
            secret = get_secret_store().get(sub.secret_ref)
        except Exception as exc:
            row.last_error = f"secret unreadable: {exc}"
            return await self._fail(row, sub)
        import json as _json

        body = _json.dumps(
            {
                "id": row.id,
                "type": row.event_type,
                "created_at": row.created_at.isoformat(),
                "data": row.payload_json,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        signature = webhook_signature(secret, body)
        ok = await _post(
            sub.url,
            body,
            headers={
                "Content-Type": "application/json",
                "X-Prodavan-Signature": signature,
                "X-Prodavan-Event": row.event_type,
                "X-Prodavan-Delivery": row.id,
            },
        )
        if ok is None:
            return await self._fail(row, sub)
        row.response_status = ok
        if 200 <= ok < 300:
            row.status = "delivered"
            row.delivered_at = datetime.now(UTC)
            row.leased_by = None
            row.lease_until = None
            sub.fail_count = 0
            return True
        row.last_error = f"http {ok}"
        return await self._fail(row, sub)

    async def _fail(self, row: WebhookDeliveryRow, sub: WebhookSubscriptionRow) -> bool:
        row.attempts += 1
        row.leased_by = None
        row.lease_until = None
        sub.fail_count = (sub.fail_count or 0) + 1
        if row.attempts >= _MAX_ATTEMPTS:
            row.status = "exhausted"
            if sub.fail_count >= _AUTO_DISABLE_AFTER:
                sub.enabled = False
                logger.warning(
                    "webhook subscription auto-disabled after %s failures: %s",
                    sub.fail_count,
                    sub.id,
                )
            return False
        row.status = "pending"
        row.next_attempt_at = datetime.now(UTC) + timedelta(seconds=_BACKOFF[row.attempts - 1])
        return False


async def _post(url: str, body: bytes, *, headers: dict[str, str]) -> int | None:
    """POST the webhook body; returns the HTTP status, None on transport error."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=_DELIVERY_TIMEOUT_S, follow_redirects=False) as client:
            resp = await client.post(url, content=body, headers=headers)
            return resp.status_code
    except Exception as exc:
        logger.info("webhook post failed url=%s err=%s", url, exc)
        return None
