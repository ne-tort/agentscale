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
    if envelope.event_id and not await store.mark_event_processed(envelope.event_id):
        return
    await MetricsCommand().ingest_envelope(envelope)
