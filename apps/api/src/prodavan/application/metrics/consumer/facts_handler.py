"""Ingest metrics.* fact events (non-pod)."""

from __future__ import annotations

import logging

from prodavan.application.metrics.adapters.redis_metrics_store import build_metrics_store
from prodavan.application.metrics.command import MetricsCommand
from prodavan.core.events.envelope import EventEnvelope
from prodavan.domain.metrics.types import METRICS_EVENT_TYPES

logger = logging.getLogger(__name__)

_POD_TYPES = frozenset({"pod.metrics.sample", "pod.metrics.degraded"})


async def handle_facts_envelope(envelope: EventEnvelope) -> None:
    if envelope.bus != "metrics":
        return
    if envelope.event_type not in METRICS_EVENT_TYPES:
        return
    if envelope.event_type in _POD_TYPES:
        return
    store = build_metrics_store()
    if envelope.event_id and await store.is_event_complete(envelope.event_id):
        return
    if envelope.event_id and not await store.claim_event(envelope.event_id):
        raise RuntimeError(f"metrics event is already claimed: {envelope.event_id}")
    try:
        await MetricsCommand().ingest_envelope(envelope)
        if envelope.event_id:
            await store.complete_event(envelope.event_id)
    except Exception:
        # Release the claim so Kafka redelivery can retry the event.
        if envelope.event_id:
            await store.release_event_claim(envelope.event_id)
        raise
