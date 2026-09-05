"""Metrics consumer envelope router."""

from __future__ import annotations

from prodavan.application.metrics.consumer.facts_handler import handle_facts_envelope
from prodavan.application.metrics.consumer.pod_metrics_handler import handle_pod_metrics_envelope
from prodavan.application.metrics.consumer.presence_handler import handle_presence_envelope
from prodavan.application.metrics.consumer.relation_handler import handle_relation_envelope
from prodavan.core.events.envelope import EventEnvelope


async def handle_metrics_envelope(envelope: EventEnvelope) -> None:
    if envelope.bus == "platform":
        # Presence from auth.* plus project.created → projects_total seed.
        await handle_presence_envelope(envelope)
        await handle_platform_entity_facts(envelope)
        return
    if envelope.bus in {"relation_event", "relation"}:
        await handle_relation_envelope(envelope)
        return
    if envelope.bus == "metrics":
        if envelope.event_type.startswith("pod.metrics."):
            await handle_pod_metrics_envelope(envelope)
        else:
            await handle_facts_envelope(envelope)


async def handle_platform_entity_facts(envelope: EventEnvelope) -> None:
    """Seed counters from platform lifecycle events when relations are absent."""
    if envelope.event_type != "project.created":
        return
    from prodavan.application.metrics.accumulator import MetricsAccumulator
    from prodavan.application.metrics.adapters.redis_counter_store import build_counter_store
    from prodavan.domain.metrics.types import (
        ENTITY_CABINET,
        ENTITY_COMPANY,
        ENTITY_PROJECT,
        METRIC_PROJECTS_TOTAL,
    )

    project_id = (envelope.project_id or "").strip()
    cabinet_id = (envelope.cabinet_id or "").strip()
    company_id = (envelope.company_id or "").strip()
    if not project_id:
        return
    store = build_counter_store()
    if cabinet_id:
        await store.put_link(
            parent_type=ENTITY_CABINET,
            parent_id=cabinet_id,
            child_type=ENTITY_PROJECT,
            child_id=project_id,
        )
    if company_id and cabinet_id:
        await store.put_link(
            parent_type=ENTITY_COMPANY,
            parent_id=company_id,
            child_type=ENTITY_CABINET,
            child_id=cabinet_id,
        )
    acc = MetricsAccumulator(store)
    target_type = ENTITY_CABINET if cabinet_id else ENTITY_COMPANY
    target_id = cabinet_id or company_id
    if not target_id:
        return
    await acc.apply_counter_delta(
        metric=METRIC_PROJECTS_TOTAL,
        entity_type=target_type,
        entity_id=target_id,
        delta=1,
        company_id=company_id or None,
        cabinet_id=cabinet_id or None,
        project_id=project_id,
        at=envelope.occurred_at,
    )
