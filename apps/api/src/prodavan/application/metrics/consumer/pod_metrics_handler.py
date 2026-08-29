"""Pod runtime metrics ingest handler."""

from __future__ import annotations

import logging

from prodavan.application.metrics.adapters.redis_metrics_store import build_metrics_store
from prodavan.application.metrics.command import MetricsCommand
from prodavan.core.events.envelope import EventEnvelope
from prodavan.domain.metrics.types import METRICS_EVENT_TYPES
from prodavan.infrastructure.persistence.database import get_session_factory

logger = logging.getLogger(__name__)


async def handle_pod_metrics_envelope(envelope: EventEnvelope) -> None:
    if envelope.bus != "metrics":
        return
    if envelope.event_type not in METRICS_EVENT_TYPES:
        return
    store = build_metrics_store()
    if not await store.mark_event_processed(envelope.event_id):
        return
    factory = get_session_factory()
    async with factory() as session:
        await MetricsCommand(session, store=store).ingest_envelope(envelope)
