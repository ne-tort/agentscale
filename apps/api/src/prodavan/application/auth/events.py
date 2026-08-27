"""Fire-and-forget auth lifecycle events on the platform Kafka bus (no PG outbox)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from prodavan.core.events.envelope import platform_envelope
from prodavan.core.infra.kafka_manager import get_kafka_manager_optional

logger = logging.getLogger(__name__)

AUTH_EVENT_TYPES = frozenset(
    {
        "auth.login",
        "auth.first_login",
        "auth.login_failed",
        "auth.token_refreshed",
        "auth.logout",
    }
)


async def publish_auth_event(event_type: str, payload: dict[str, Any]) -> None:
    if event_type not in AUTH_EVENT_TYPES:
        logger.warning("unknown auth event_type=%s ignored", event_type)
        return
    envelope = platform_envelope(
        event_id=str(uuid.uuid4()),
        event_type=event_type,
        payload=payload,
    )
    manager = get_kafka_manager_optional()
    if manager is None:
        logger.debug("auth event %s skipped (KafkaManager not started)", event_type)
        return
    try:
        ok = await manager.publish(envelope)
        if not ok:
            logger.warning("auth event %s publish returned false", event_type)
    except Exception:
        logger.exception("auth event %s publish failed", event_type)
