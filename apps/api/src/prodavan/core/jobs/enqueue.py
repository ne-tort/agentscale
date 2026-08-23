"""Job helpers — enqueue via Celery when enabled."""

from __future__ import annotations

import logging
from typing import Any

from prodavan.core.jobs import names as job_names

logger = logging.getLogger(__name__)


def enqueue_trigger_drain() -> dict[str, Any]:
    """Enqueue periodic drain tick (no-op if Celery not configured)."""
    from prodavan.core.infra.worker_manager import get_worker_manager

    mgr = get_worker_manager()
    if mgr is None or not mgr.enabled:
        return {"enqueued": False, "reason": "celery_disabled"}
    mgr.send_task(job_names.TRIGGER_DRAIN)
    return {"enqueued": True, "task": job_names.TRIGGER_DRAIN}


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
    mgr.send_task(job_names.REMATERIALIZE_PROJECT, args=[project_id])
    return {"enqueued": True, "task": job_names.REMATERIALIZE_PROJECT, "project_id": project_id}
