"""Event bus helpers — publish via KafkaManager (C-EVENT-BUS)."""

from __future__ import annotations

import logging
from typing import Any

from prodavan.core.events.envelope import (
    EventEnvelope,
    platform_envelope,
    project_trigger_envelope,
)

logger = logging.getLogger(__name__)


async def publish_envelope(envelope: EventEnvelope) -> bool:
    """Best-effort publish; never raises to callers (PG remains transitional SoT)."""
    from prodavan.core.infra.kafka_manager import get_kafka_manager_optional

    mgr = get_kafka_manager_optional()
    if mgr is None:
        return False
    try:
        return await mgr.publish(envelope)
    except Exception:
        logger.exception("event bus publish failed bus=%s id=%s", envelope.bus, envelope.event_id)
        return False


async def publish_platform_event(
    *,
    event_id: str,
    event_type: str,
    company_id: str | None = None,
    project_id: str | None = None,
    cabinet_id: str | None = None,
    payload: dict[str, Any] | None = None,
    occurred_at: str | None = None,
) -> bool:
    return await publish_envelope(
        platform_envelope(
            event_id=event_id,
            event_type=event_type,
            company_id=company_id,
            project_id=project_id,
            cabinet_id=cabinet_id,
            payload=payload,
            occurred_at=occurred_at,
        )
    )


async def publish_project_trigger(
    *,
    event_id: str,
    kind: str,
    project_id: str,
    company_id: str | None = None,
    cabinet_id: str | None = None,
    payload: dict[str, Any] | None = None,
    occurred_at: str | None = None,
) -> bool:
    return await publish_envelope(
        project_trigger_envelope(
            event_id=event_id,
            kind=kind,
            project_id=project_id,
            company_id=company_id,
            cabinet_id=cabinet_id,
            payload=payload,
            occurred_at=occurred_at,
        )
    )
