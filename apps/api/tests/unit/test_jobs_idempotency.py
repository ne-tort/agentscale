"""Unit tests — Celery task_id helpers (C-JOBS idempotency)."""

from __future__ import annotations

from prodavan.core.jobs import names as job_names
from prodavan.core.jobs.idempotency import (
    dispatch_trigger_task_id,
    rematerialize_project_task_id,
)


def test_dispatch_trigger_task_id_stable() -> None:
    assert dispatch_trigger_task_id("trig-1") == f"{job_names.DISPATCH_TRIGGER}:trig-1"
    assert dispatch_trigger_task_id("  trig-1  ") == f"{job_names.DISPATCH_TRIGGER}:trig-1"


def test_rematerialize_project_task_id_stable() -> None:
    assert rematerialize_project_task_id("p1") == f"{job_names.REMATERIALIZE_PROJECT}:p1"
