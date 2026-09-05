"""Build entity link graph from relation.* Kafka events."""

from __future__ import annotations

import logging

from prodavan.application.metrics.accumulator import MetricsAccumulator
from prodavan.application.metrics.adapters.redis_counter_store import build_counter_store
from prodavan.core.events.envelope import EventEnvelope
from prodavan.domain.metrics.types import (
    ENTITY_CABINET,
    ENTITY_COMPANY,
    ENTITY_EMPLOYEE,
    ENTITY_PROJECT,
    METRIC_CABINETS_TOTAL,
    METRIC_EMPLOYEES_TOTAL,
    METRIC_PROJECTS_TOTAL,
)
from prodavan.domain.relations import RELATION_GRANTED, RELATION_REVOKED

logger = logging.getLogger(__name__)


async def handle_relation_envelope(envelope: EventEnvelope) -> None:
    if envelope.bus not in {"relation_event", "relation"}:
        return
    if envelope.event_type not in {RELATION_GRANTED, RELATION_REVOKED, "relation.replaced"}:
        return
    payload = dict(envelope.payload or {})
    subject_kind = str(payload.get("subject_kind") or "").strip().lower()
    subject_id = str(payload.get("subject_id") or "").strip()
    object_kind = str(payload.get("object_kind") or "").strip().lower()
    object_id = str(payload.get("object_id") or "").strip()
    if not subject_id or not object_id:
        return

    store = build_counter_store()
    acc = MetricsAccumulator(store)
    granted = envelope.event_type == RELATION_GRANTED
    delta = 1 if granted else -1

    # Normalize kinds to metrics entity names.
    sk = _norm_kind(subject_kind)
    ok = _norm_kind(object_kind)

    if granted:
        # Child → parent: subject is often employee, object is company/cabinet.
        if sk == ENTITY_EMPLOYEE and ok == ENTITY_COMPANY:
            await store.put_link(
                parent_type=ENTITY_COMPANY,
                parent_id=object_id,
                child_type=ENTITY_EMPLOYEE,
                child_id=subject_id,
            )
            await acc.apply_counter_delta(
                metric=METRIC_EMPLOYEES_TOTAL,
                entity_type=ENTITY_COMPANY,
                entity_id=object_id,
                delta=delta,
                company_id=object_id,
            )
        elif sk == ENTITY_EMPLOYEE and ok == ENTITY_CABINET:
            await store.put_link(
                parent_type=ENTITY_CABINET,
                parent_id=object_id,
                child_type=ENTITY_EMPLOYEE,
                child_id=subject_id,
            )
            company_id = str(payload.get("via_company_id") or envelope.company_id or "").strip() or None
            if company_id:
                await store.put_link(
                    parent_type=ENTITY_COMPANY,
                    parent_id=company_id,
                    child_type=ENTITY_CABINET,
                    child_id=object_id,
                )
            await acc.apply_counter_delta(
                metric=METRIC_EMPLOYEES_TOTAL,
                entity_type=ENTITY_CABINET,
                entity_id=object_id,
                delta=delta,
                company_id=company_id,
                cabinet_id=object_id,
            )
        elif sk == ENTITY_PROJECT and ok == ENTITY_CABINET:
            await store.put_link(
                parent_type=ENTITY_CABINET,
                parent_id=object_id,
                child_type=ENTITY_PROJECT,
                child_id=subject_id,
            )
            company_id = str(envelope.company_id or "").strip() or None
            if company_id:
                await store.put_link(
                    parent_type=ENTITY_COMPANY,
                    parent_id=company_id,
                    child_type=ENTITY_CABINET,
                    child_id=object_id,
                )
            await acc.apply_counter_delta(
                metric=METRIC_PROJECTS_TOTAL,
                entity_type=ENTITY_CABINET,
                entity_id=object_id,
                delta=delta,
                company_id=company_id,
                cabinet_id=object_id,
            )
        elif sk == ENTITY_CABINET and ok == ENTITY_COMPANY:
            await store.put_link(
                parent_type=ENTITY_COMPANY,
                parent_id=object_id,
                child_type=ENTITY_CABINET,
                child_id=subject_id,
            )
            await acc.apply_counter_delta(
                metric=METRIC_CABINETS_TOTAL,
                entity_type=ENTITY_COMPANY,
                entity_id=object_id,
                delta=delta,
                company_id=object_id,
            )
    else:
        if sk == ENTITY_EMPLOYEE and ok == ENTITY_COMPANY:
            await store.remove_link(
                parent_type=ENTITY_COMPANY,
                parent_id=object_id,
                child_type=ENTITY_EMPLOYEE,
                child_id=subject_id,
            )
            await acc.apply_counter_delta(
                metric=METRIC_EMPLOYEES_TOTAL,
                entity_type=ENTITY_COMPANY,
                entity_id=object_id,
                delta=delta,
                company_id=object_id,
            )
        elif sk == ENTITY_EMPLOYEE and ok == ENTITY_CABINET:
            await store.remove_link(
                parent_type=ENTITY_CABINET,
                parent_id=object_id,
                child_type=ENTITY_EMPLOYEE,
                child_id=subject_id,
            )
            await acc.apply_counter_delta(
                metric=METRIC_EMPLOYEES_TOTAL,
                entity_type=ENTITY_CABINET,
                entity_id=object_id,
                delta=delta,
                company_id=str(payload.get("via_company_id") or envelope.company_id or "") or None,
                cabinet_id=object_id,
            )
        elif sk == ENTITY_PROJECT and ok == ENTITY_CABINET:
            await store.remove_link(
                parent_type=ENTITY_CABINET,
                parent_id=object_id,
                child_type=ENTITY_PROJECT,
                child_id=subject_id,
            )
            await acc.apply_counter_delta(
                metric=METRIC_PROJECTS_TOTAL,
                entity_type=ENTITY_CABINET,
                entity_id=object_id,
                delta=delta,
                company_id=str(envelope.company_id or "") or None,
                cabinet_id=object_id,
            )
        elif sk == ENTITY_CABINET and ok == ENTITY_COMPANY:
            await store.remove_link(
                parent_type=ENTITY_COMPANY,
                parent_id=object_id,
                child_type=ENTITY_CABINET,
                child_id=subject_id,
            )
            await acc.apply_counter_delta(
                metric=METRIC_CABINETS_TOTAL,
                entity_type=ENTITY_COMPANY,
                entity_id=object_id,
                delta=delta,
                company_id=object_id,
            )


def _norm_kind(kind: str) -> str:
    k = kind.lower().strip()
    aliases = {
        "employee": ENTITY_EMPLOYEE,
        "company": ENTITY_COMPANY,
        "cabinet": ENTITY_CABINET,
        "project": ENTITY_PROJECT,
        "cabinet_instance": ENTITY_CABINET,
    }
    return aliases.get(k, k)
