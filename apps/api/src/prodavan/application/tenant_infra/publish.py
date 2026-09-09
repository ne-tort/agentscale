"""Publish tenant_infra + metrics events."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def emit_tenant_infra_event(
    *,
    session: AsyncSession | None,
    event_type: str,
    company_id: str | None,
    cabinet_id: str | None,
    project_id: str | None,
    payload: dict[str, Any],
) -> None:
    event_id = str(uuid.uuid4())
    if session is not None:
        from prodavan.core.events.deferred import schedule_platform_event_publish

        schedule_platform_event_publish(
            session,
            event_id=event_id,
            event_type=event_type,
            company_id=company_id,
            project_id=project_id,
            cabinet_id=cabinet_id,
            payload=payload,
        )
        return
    from prodavan.core.events.bus import publish_envelope
    from prodavan.core.events.envelope import platform_envelope

    try:
        await publish_envelope(
            platform_envelope(
                event_id=event_id,
                event_type=event_type,
                company_id=company_id,
                project_id=project_id,
                cabinet_id=cabinet_id,
                payload=payload,
            )
        )
    except Exception:
        logger.exception("tenant_infra event publish failed type=%s", event_type)


async def emit_cache_op_metric(
    *,
    session: AsyncSession | None,
    company_id: str | None,
    cabinet_id: str | None,
    project_id: str | None,
    op: str,
) -> None:
    from prodavan.domain.metrics.types import ENTITY_COMPANY, ENTITY_PROJECT

    entity_type = ENTITY_PROJECT if project_id else ENTITY_COMPANY
    entity_id = project_id or company_id or "platform"
    payload: dict[str, Any] = {
        "metric": "tenant_infra_cache_ops",
        "entity_type": entity_type,
        "entity_id": entity_id,
        "delta": 1,
        "company_id": company_id,
        "cabinet_id": cabinet_id,
        "op": op,
    }
    if session is not None:
        from prodavan.core.events.deferred import schedule_metrics_event_publish

        schedule_metrics_event_publish(
            session,
            event_id=str(uuid.uuid4()),
            event_type="metrics.counter.delta",
            company_id=company_id,
            project_id=project_id,
            cabinet_id=cabinet_id,
            payload=payload,
        )
        return
    from prodavan.core.events.bus import publish_envelope
    from prodavan.core.events.envelope import metrics_envelope

    try:
        await publish_envelope(
            metrics_envelope(
                event_id=str(uuid.uuid4()),
                event_type="metrics.counter.delta",
                company_id=company_id,
                project_id=project_id,
                cabinet_id=cabinet_id,
                payload=payload,
            )
        )
    except Exception:
        logger.exception("tenant_infra metrics publish failed op=%s", op)
