"""Job helpers — enqueue via Celery when enabled."""

from __future__ import annotations

import logging
from typing import Any

from prodavan.core.jobs import names as job_names
from prodavan.core.jobs.idempotency import (
    dispatch_trigger_task_id,
    rematerialize_project_task_id,
)

logger = logging.getLogger(__name__)


def enqueue_trigger_drain() -> dict[str, Any]:
    """Enqueue periodic drain tick (no-op if Celery not configured).

    Coalesced by Kafka consumer Redis lock / beat — no fixed Celery task_id.
    """
    from prodavan.core.infra.worker_manager import get_worker_manager

    mgr = get_worker_manager()
    if mgr is None or not mgr.enabled:
        return {"enqueued": False, "reason": "celery_disabled"}
    mgr.send_task(job_names.TRIGGER_DRAIN)
    return {"enqueued": True, "task": job_names.TRIGGER_DRAIN}


def enqueue_dispatch_trigger(trigger_id: str) -> dict[str, Any]:
    """Enqueue per-id trigger dispatch (Kafka consumer cutover mode)."""
    from prodavan.core.infra.worker_manager import get_worker_manager

    tid = (trigger_id or "").strip()
    if not tid:
        return {"enqueued": False, "reason": "missing_trigger_id"}
    mgr = get_worker_manager()
    if mgr is None or not mgr.enabled:
        return {"enqueued": False, "reason": "celery_disabled", "trigger_id": tid}
    task_id = dispatch_trigger_task_id(tid)
    mgr.send_task(job_names.DISPATCH_TRIGGER, args=[tid], task_id=task_id)
    return {
        "enqueued": True,
        "task": job_names.DISPATCH_TRIGGER,
        "trigger_id": tid,
        "task_id": task_id,
    }


def enqueue_idle_pause_sweep() -> dict[str, Any]:
    from prodavan.core.infra.worker_manager import get_worker_manager

    mgr = get_worker_manager()
    if mgr is None or not mgr.enabled:
        return {"enqueued": False, "reason": "celery_disabled"}
    mgr.send_task(job_names.IDLE_PAUSE_SWEEP)
    return {"enqueued": True, "task": job_names.IDLE_PAUSE_SWEEP}


def enqueue_rematerialize_project(project_id: str) -> dict[str, Any]:
    from prodavan.core.infra.worker_manager import get_worker_manager

    mgr = get_worker_manager()
    if mgr is None or not mgr.enabled:
        return {"enqueued": False, "reason": "celery_disabled", "project_id": project_id}
    pid = (project_id or "").strip()
    task_id = rematerialize_project_task_id(pid)
    mgr.send_task(job_names.REMATERIALIZE_PROJECT, args=[pid], task_id=task_id)
    return {
        "enqueued": True,
        "task": job_names.REMATERIALIZE_PROJECT,
        "project_id": pid,
        "task_id": task_id,
    }
