"""Celery task idempotency helpers (C-JOBS).

Stable ``task_id`` values let Celery replace duplicate enqueues for the same
logical unit of work (dispatch one trigger, rematerialize one project).

Periodic jobs (``trigger_drain``, ``idle_pause_sweep``) are coalesced via Redis
lock / beat schedule — they intentionally do **not** use fixed task ids.
"""

from __future__ import annotations

from prodavan.core.jobs import names as job_names


def dispatch_trigger_task_id(trigger_id: str) -> str:
    tid = (trigger_id or "").strip()
    return f"{job_names.DISPATCH_TRIGGER}:{tid}"


def rematerialize_project_task_id(project_id: str) -> str:
    pid = (project_id or "").strip()
    return f"{job_names.REMATERIALIZE_PROJECT}:{pid}"


def wipe_cabinet_packages_task_id(cabinet_id: str) -> str:
    cid = (cabinet_id or "").strip()
    return f"{job_names.WIPE_CABINET_PACKAGES}:{cid}"


def wipe_project_tree_task_id(workspace_key: str) -> str:
    key = (workspace_key or "").strip()
    return f"{job_names.WIPE_PROJECT_TREE}:{key}"


def cascade_company_deleted_task_id(company_id: str) -> str:
    cid = (company_id or "").strip()
    return f"{job_names.CASCADE_COMPANY_DELETED}:{cid}"
