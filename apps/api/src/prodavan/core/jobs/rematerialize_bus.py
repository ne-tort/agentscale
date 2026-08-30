"""Kafka bus cutover for project rematerialize jobs (C-JOBS / C-EVENT-BUS)."""

from __future__ import annotations

import logging
from typing import Any

from prodavan.config.settings import settings
from prodavan.core.events.envelope import platform_envelope
from prodavan.core.jobs.enqueue import enqueue_rematerialize_project
from prodavan.core.jobs.idempotency import rematerialize_project_task_id

logger = logging.getLogger(__name__)

REMATERIALIZE_REQUESTED = "project.rematerialize.requested"


def rematerialize_via_bus_enabled() -> bool:
    return bool(settings.kafka_rematerialize_via_bus and settings.kafka_consumer_enabled)


async def publish_rematerialize_requested(
    *,
    project_id: str,
    cabinet_id: str | None = None,
    module_id: str | None = None,
    source: str = "cabinet_module",
) -> dict[str, Any]:
    """Publish rematerialize command to platform Kafka topic (no PG outbox row)."""
    from prodavan.core.events.bus import publish_envelope

    pid = (project_id or "").strip()
    if not pid:
        return {"published": False, "reason": "missing_project_id"}

    payload: dict[str, Any] = {"source": source}
    if module_id:
        payload["module_id"] = module_id

    envelope = platform_envelope(
        event_id=rematerialize_project_task_id(pid),
        event_type=REMATERIALIZE_REQUESTED,
        project_id=pid,
        cabinet_id=cabinet_id,
        payload=payload,
    )
    ok = await publish_envelope(envelope)
    return {
        "published": ok,
        "via_bus": True,
        "project_id": pid,
        "event_id": envelope.event_id,
    }


async def request_rematerialize_project(
    project_id: str,
    *,
    cabinet_id: str | None = None,
    module_id: str | None = None,
    source: str = "cabinet_module",
) -> dict[str, Any]:
    """Publish to Kafka when bus cutover is on; otherwise enqueue Celery directly."""
    pid = (project_id or "").strip()
    if not pid:
        return {"enqueued": False, "reason": "missing_project_id"}

    if rematerialize_via_bus_enabled():
        out = await publish_rematerialize_requested(
            project_id=pid,
            cabinet_id=cabinet_id,
            module_id=module_id,
            source=source,
        )
        if out.get("published"):
            return {
                "enqueued": True,
                "via_bus": True,
                "project_id": pid,
                "task_id": out.get("event_id"),
            }
        logger.warning(
            "rematerialize bus publish failed project_id=%s; falling back to direct enqueue",
            pid,
        )

    return enqueue_rematerialize_project(pid)


def handle_rematerialize_requested_envelope(data: dict[str, Any]) -> str | None:
    """Extract project_id from a platform rematerialize envelope."""
    if data.get("bus") != "platform":
        return None
    if data.get("event_type") != REMATERIALIZE_REQUESTED:
        return None
    pid = str(data.get("project_id") or "").strip()
    if pid:
        return pid
    payload = data.get("payload")
    if isinstance(payload, dict):
        return str(payload.get("project_id") or "").strip() or None
    return None


async def apply_rematerialize_requested_envelope(data: dict[str, Any]) -> dict[str, Any]:
    """Consumer handler — enqueue Celery rematerialize for one project."""
    pid = handle_rematerialize_requested_envelope(data)
    if not pid:
        return {"ok": False, "reason": "not_rematerialize_envelope"}
    result = enqueue_rematerialize_project(pid)
    return {"ok": True, "project_id": pid, **result}
