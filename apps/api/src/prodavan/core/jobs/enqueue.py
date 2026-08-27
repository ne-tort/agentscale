"""Job helpers — enqueue via Celery when enabled."""

from __future__ import annotations

import logging
from typing import Any

from prodavan.core.jobs import names as job_names
from prodavan.core.jobs.idempotency import (
    dispatch_trigger_task_id,
    rematerialize_project_task_id,
    wipe_cabinet_packages_task_id,
    wipe_project_tree_task_id,
)

logger = logging.getLogger(__name__)


def enqueue_trigger_drain() -> dict[str, Any]:
    """Enqueue periodic drain tick (no-op if Celery not configured).

    Coalesced by Celery task Redis lock / Kafka kick lock / beat — no fixed task_id.
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


def enqueue_wipe_cabinet_packages(cabinet_id: str) -> dict[str, Any]:
    """Retry GC for cabinet package blobs after incomplete archive wipe."""
    from prodavan.core.infra.worker_manager import get_worker_manager

    cid = (cabinet_id or "").strip()
    if not cid:
        return {"enqueued": False, "reason": "missing_cabinet_id"}
    mgr = get_worker_manager()
    if mgr is None or not mgr.enabled:
        return {"enqueued": False, "reason": "celery_disabled", "cabinet_id": cid}
    task_id = wipe_cabinet_packages_task_id(cid)
    mgr.send_task(job_names.WIPE_CABINET_PACKAGES, args=[cid], task_id=task_id)
    return {
        "enqueued": True,
        "task": job_names.WIPE_CABINET_PACKAGES,
        "cabinet_id": cid,
        "task_id": task_id,
    }


def enqueue_wipe_project_tree(workspace_key: str) -> dict[str, Any]:
    """Retry GC for project object-store tree after incomplete delete wipe."""
    from prodavan.core.infra.worker_manager import get_worker_manager

    key = (workspace_key or "").strip()
    if not key:
        return {"enqueued": False, "reason": "missing_workspace_key"}
    mgr = get_worker_manager()
    if mgr is None or not mgr.enabled:
        return {"enqueued": False, "reason": "celery_disabled", "workspace_key": key}
    task_id = wipe_project_tree_task_id(key)
    mgr.send_task(job_names.WIPE_PROJECT_TREE, args=[key], task_id=task_id)
    return {
        "enqueued": True,
        "task": job_names.WIPE_PROJECT_TREE,
        "workspace_key": key,
        "task_id": task_id,
    }


def enqueue_gc_orphan_cabinet_schemas(*, dry_run: bool = False, limit: int = 50) -> dict[str, Any]:
    """Enqueue singleton orphan schema GC (coalesced via fixed Celery task_id)."""
    from prodavan.core.infra.worker_manager import get_worker_manager

    mgr = get_worker_manager()
    if mgr is None or not mgr.enabled:
        return {"enqueued": False, "reason": "celery_disabled"}
    task_id = job_names.GC_ORPHAN_CABINET_SCHEMAS
    mgr.send_task(
        job_names.GC_ORPHAN_CABINET_SCHEMAS,
        kwargs={"dry_run": bool(dry_run), "limit": int(limit)},
        task_id=task_id,
    )
    return {
        "enqueued": True,
        "task": job_names.GC_ORPHAN_CABINET_SCHEMAS,
        "task_id": task_id,
        "dry_run": bool(dry_run),
        "limit": int(limit),
    }


def enqueue_gc_orphan_blobs(
    *,
    dry_run: bool = False,
    limit: int = 50,
    scan_limit: int = 500,
) -> dict[str, Any]:
    """Enqueue singleton orphan blob prefix GC."""
    from prodavan.core.infra.worker_manager import get_worker_manager

    mgr = get_worker_manager()
    if mgr is None or not mgr.enabled:
        return {"enqueued": False, "reason": "celery_disabled"}
    task_id = job_names.GC_ORPHAN_BLOBS
    mgr.send_task(
        job_names.GC_ORPHAN_BLOBS,
        kwargs={
            "dry_run": bool(dry_run),
            "limit": int(limit),
            "scan_limit": int(scan_limit),
        },
        task_id=task_id,
    )
    return {
        "enqueued": True,
        "task": job_names.GC_ORPHAN_BLOBS,
        "task_id": task_id,
        "dry_run": bool(dry_run),
        "limit": int(limit),
        "scan_limit": int(scan_limit),
    }


def enqueue_apply_auth_user_registered(payload: dict[str, Any]) -> dict[str, Any]:
    """Enqueue Identity bind of keycloak_sub from auth.user.registered."""
    from prodavan.core.infra.worker_manager import get_worker_manager

    mgr = get_worker_manager()
    if mgr is None or not mgr.enabled:
        return {"enqueued": False, "reason": "celery_disabled"}
    mgr.send_task(job_names.APPLY_AUTH_USER_REGISTERED, args=[payload])
    return {"enqueued": True, "task": job_names.APPLY_AUTH_USER_REGISTERED}


def enqueue_cascade_company_deleted(company_id: str, *, actor_sub: str = "system") -> dict[str, Any]:
    """Enqueue async cascade after company soft-delete (idempotent task_id)."""
    from prodavan.core.infra.worker_manager import get_worker_manager
    from prodavan.core.jobs.idempotency import cascade_company_deleted_task_id

    cid = (company_id or "").strip()
    if not cid:
        return {"enqueued": False, "reason": "missing_company_id"}
    mgr = get_worker_manager()
    if mgr is None or not mgr.enabled:
        return {"enqueued": False, "reason": "celery_disabled", "company_id": cid}
    task_id = cascade_company_deleted_task_id(cid)
    mgr.send_task(
        job_names.CASCADE_COMPANY_DELETED,
        args=[cid],
        kwargs={"actor_sub": actor_sub or "system"},
        task_id=task_id,
    )
    return {
        "enqueued": True,
        "task": job_names.CASCADE_COMPANY_DELETED,
        "company_id": cid,
        "task_id": task_id,
    }

