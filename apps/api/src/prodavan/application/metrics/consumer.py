"""Kafka consumer handlers — auth presence on platform events bus."""

from __future__ import annotations

import logging
from typing import Any

from prodavan.application.metrics.presence_resolver import PresenceResolver
from prodavan.application.metrics.presence_store import clear_presence, set_presence
from prodavan.core.events.envelope import EventEnvelope
from prodavan.infrastructure.persistence.database import get_session_factory

logger = logging.getLogger(__name__)

PRESENCE_SET_EVENTS = frozenset(
    {
        "auth.login",
        "auth.token_refreshed",
        "auth.first_login",
    }
)
PRESENCE_CLEAR_EVENTS = frozenset({"auth.logout"})


async def handle_platform_envelope(envelope: EventEnvelope) -> None:
    """Process platform auth events for Redis presence."""
    if envelope.bus != "platform":
        return
    event_type = envelope.event_type
    if event_type not in PRESENCE_SET_EVENTS and event_type not in PRESENCE_CLEAR_EVENTS:
        return
    await handle_auth_event(event_type, dict(envelope.payload or {}))


async def handle_auth_event(event_type: str, payload: dict[str, Any]) -> None:
    factory = get_session_factory()
    async with factory() as session:
        resolved = await PresenceResolver(session).resolve_auth_payload(payload)
    if resolved is None:
        logger.debug("presence skip unresolved event=%s sub=%s", event_type, payload.get("sub"))
        return
    if event_type in PRESENCE_SET_EVENTS:
        await set_presence(resolved.kind, resolved.entity_id)
        return
    if event_type in PRESENCE_CLEAR_EVENTS:
        await clear_presence(resolved.kind, resolved.entity_id)
