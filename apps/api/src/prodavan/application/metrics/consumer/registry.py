"""Metrics consumer envelope router."""

from __future__ import annotations

from prodavan.application.metrics.consumer.pod_metrics_handler import handle_pod_metrics_envelope
from prodavan.application.metrics.consumer.presence_handler import handle_presence_envelope
from prodavan.core.events.envelope import EventEnvelope


async def handle_metrics_envelope(envelope: EventEnvelope) -> None:
    if envelope.bus == "platform":
        await handle_presence_envelope(envelope)
        return
    if envelope.bus == "metrics":
        await handle_pod_metrics_envelope(envelope)
