"""Publish metrics facts to Kafka (deferred when session present)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def schedule_usage_turn(
    session: AsyncSession,
    *,
    project_id: str,
    company_id: str | None,
    cabinet_id: str | None,
    employee_id: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    provider: str | None = None,
    model: str | None = None,
    request_only: bool = False,
) -> None:
    """Emit metrics.usage.turn — one user→agent turn (or request_only at user_message)."""
    from prodavan.core.events.deferred import schedule_metrics_event_publish

    payload: dict[str, Any] = {
        "project_id": project_id,
        "company_id": company_id,
        "cabinet_id": cabinet_id,
        "employee_id": employee_id,
        "request_only": request_only,
    }
    if not request_only:
        payload["input_tokens"] = int(input_tokens or 0)
        payload["output_tokens"] = int(output_tokens or 0)
        payload["provider"] = provider
        payload["model"] = model
    schedule_metrics_event_publish(
        session,
        event_id=str(uuid.uuid4()),
        event_type="metrics.usage.turn",
        company_id=company_id,
        project_id=project_id,
        cabinet_id=cabinet_id,
        payload=payload,
    )


def schedule_agent_request(
    session: AsyncSession,
    *,
    project_id: str,
    company_id: str | None,
    cabinet_id: str | None,
    employee_id: str | None = None,
) -> None:
    """Count one AI request (user message) without waiting for usage tokens."""
    from prodavan.core.events.deferred import schedule_metrics_event_publish
    from prodavan.domain.metrics.types import (
        ENTITY_PROJECT,
        METRIC_AGENT_REQUESTS,
    )

    schedule_metrics_event_publish(
        session,
        event_id=str(uuid.uuid4()),
        event_type="metrics.counter.delta",
        company_id=company_id,
        project_id=project_id,
        cabinet_id=cabinet_id,
        payload={
            "metric": METRIC_AGENT_REQUESTS,
            "entity_type": ENTITY_PROJECT,
            "entity_id": project_id,
            "delta": 1,
            "company_id": company_id,
            "cabinet_id": cabinet_id,
            "employee_id": employee_id,
        },
    )


def schedule_storage_snapshot(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: str,
    bytes_value: int,
    company_id: str | None = None,
    cabinet_id: str | None = None,
    project_id: str | None = None,
) -> None:
    from prodavan.core.events.deferred import schedule_metrics_event_publish

    schedule_metrics_event_publish(
        session,
        event_id=str(uuid.uuid4()),
        event_type="metrics.storage.snapshot",
        company_id=company_id,
        project_id=project_id,
        cabinet_id=cabinet_id,
        payload={
            "entity_type": entity_type,
            "entity_id": entity_id,
            "bytes": int(bytes_value),
            "company_id": company_id,
            "cabinet_id": cabinet_id,
        },
    )


async def publish_presence_heartbeat(
    *,
    employee_id: str,
    cabinet_id: str | None = None,
    company_id: str | None = None,
) -> None:
    """Immediate presence SET + metrics bus event (no PG session)."""
    from prodavan.application.metrics.presence_store import set_presence
    from prodavan.core.events.bus import publish_envelope
    from prodavan.core.events.envelope import metrics_envelope

    await set_presence("employee", employee_id)
    try:
        await publish_envelope(
            metrics_envelope(
                event_id=str(uuid.uuid4()),
                event_type="metrics.presence.heartbeat",
                company_id=company_id,
                cabinet_id=cabinet_id,
                payload={
                    "employee_id": employee_id,
                    "cabinet_id": cabinet_id,
                    "company_id": company_id,
                },
            )
        )
    except Exception:
        logger.exception("presence heartbeat publish failed employee=%s", employee_id)
