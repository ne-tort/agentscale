"""Publish Documents module domain + metrics events (DOCUM)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

EVENT_DOCUMENTS_CONVERTED = "documents.converted"
EVENT_DOCUMENTS_CREATED = "documents.created"
EVENT_DOCUMENTS_TEMPLATE_FILLED = "documents.template_filled"
EVENT_DOCUMENTS_READ = "documents.read"


async def emit_documents_event(
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
        from prodavan.core.events.deferred import schedule_document_event_publish

        schedule_document_event_publish(
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
    from prodavan.core.events.envelope import document_envelope

    try:
        await publish_envelope(
            document_envelope(
                event_id=event_id,
                event_type=event_type,
                company_id=company_id,
                project_id=project_id,
                cabinet_id=cabinet_id,
                payload=payload,
            )
        )
    except Exception:
        logger.exception("documents event publish failed type=%s", event_type)


async def emit_op_metric(
    *,
    session: AsyncSession | None,
    metric: str,
    company_id: str | None,
    cabinet_id: str | None,
    project_id: str | None,
    delta: int = 1,
) -> None:
    from prodavan.domain.metrics.types import ENTITY_COMPANY, ENTITY_PROJECT

    entity_type = ENTITY_PROJECT if project_id else ENTITY_COMPANY
    entity_id = project_id or company_id or "platform"
    payload: dict[str, Any] = {
        "metric": metric,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "delta": int(delta),
        "company_id": company_id,
        "cabinet_id": cabinet_id,
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
        logger.exception("documents metrics publish failed metric=%s", metric)
