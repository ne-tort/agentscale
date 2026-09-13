"""Publish Search Index domain + metrics events."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def emit_search_index_ensured(
    *,
    session: AsyncSession | None,
    namespace: str,
    index: str,
    company_id: str | None,
    cabinet_id: str | None,
    project_id: str | None,
    created: bool,
) -> None:
    await _emit_search(
        session=session,
        event_type="search.index.ensured",
        company_id=company_id,
        cabinet_id=cabinet_id,
        project_id=project_id,
        payload={"namespace": namespace, "index": index, "created": created},
    )


async def emit_search_index_deleted(
    *,
    session: AsyncSession | None,
    namespace: str,
    index: str,
    company_id: str | None,
    cabinet_id: str | None,
    project_id: str | None,
    deleted: bool,
) -> None:
    await _emit_search(
        session=session,
        event_type="search.index.deleted",
        company_id=company_id,
        cabinet_id=cabinet_id,
        project_id=project_id,
        payload={"namespace": namespace, "index": index, "deleted": deleted},
    )


async def emit_search_document_indexed(
    *,
    session: AsyncSession | None,
    namespace: str,
    index: str,
    company_id: str | None,
    cabinet_id: str | None,
    project_id: str | None,
    doc_id: str,
    result: str,
    count: int = 1,
) -> None:
    await _emit_search(
        session=session,
        event_type="search.document.indexed",
        company_id=company_id,
        cabinet_id=cabinet_id,
        project_id=project_id,
        payload={
            "namespace": namespace,
            "index": index,
            "doc_id": doc_id,
            "result": result,
            "count": count,
        },
    )


async def emit_search_document_deleted(
    *,
    session: AsyncSession | None,
    namespace: str,
    index: str,
    company_id: str | None,
    cabinet_id: str | None,
    project_id: str | None,
    doc_id: str,
    deleted: bool,
) -> None:
    await _emit_search(
        session=session,
        event_type="search.document.deleted",
        company_id=company_id,
        cabinet_id=cabinet_id,
        project_id=project_id,
        payload={
            "namespace": namespace,
            "index": index,
            "doc_id": doc_id,
            "deleted": deleted,
        },
    )


async def emit_op_metric(
    *,
    session: AsyncSession | None,
    metric: str,
    company_id: str | None,
    cabinet_id: str | None,
    project_id: str | None,
    delta: int = 1,
) -> None:
    from prodavan.domain.metrics.types import ENTITY_COMPANY, ENTITY_PROJECT

    entity_type = ENTITY_PROJECT if project_id else ENTITY_COMPANY
    entity_id = project_id or company_id or "platform"
    payload: dict[str, Any] = {
        "metric": metric,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "delta": int(delta),
        "company_id": company_id,
        "cabinet_id": cabinet_id,
    }
    if session is not None:
        from prodavan.core.events.deferred import schedule_metrics_event_publish

        schedule_metrics_event_publish(
            session,
            event_id=str(uuid.uuid4()),
            event_type="metrics.counter.delta",
            company_id=company_id,
            project_id=project_id,
            cabinet_id=cabinet_id,
            payload=payload,
        )
        return
    from prodavan.core.events.bus import publish_envelope
    from prodavan.core.events.envelope import metrics_envelope

    try:
        await publish_envelope(
            metrics_envelope(
                event_id=str(uuid.uuid4()),
                event_type="metrics.counter.delta",
                company_id=company_id,
                project_id=project_id,
                cabinet_id=cabinet_id,
                payload=payload,
            )
        )
    except Exception:
        logger.exception("search_index metrics publish failed metric=%s", metric)


async def _emit_search(
    *,
    session: AsyncSession | None,
    event_type: str,
    company_id: str | None,
    cabinet_id: str | None,
    project_id: str | None,
    payload: dict[str, Any],
) -> None:
    event_id = str(uuid.uuid4())
    if session is not None:
        from prodavan.core.events.deferred import schedule_search_event_publish

        schedule_search_event_publish(
            session,
            event_id=event_id,
            event_type=event_type,
            company_id=company_id,
            project_id=project_id,
            cabinet_id=cabinet_id,
            payload=payload,
        )
        return
    from prodavan.core.events.bus import publish_envelope
    from prodavan.core.events.envelope import search_envelope

    try:
        await publish_envelope(
            search_envelope(
                event_id=event_id,
                event_type=event_type,
                company_id=company_id,
                project_id=project_id,
                cabinet_id=cabinet_id,
                payload=payload,
            )
        )
    except Exception:
        logger.exception("search_index event publish failed type=%s", event_type)
