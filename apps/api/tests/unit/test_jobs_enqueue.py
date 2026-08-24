"""Unit tests — enqueue helpers pass stable Celery task_id."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from prodavan.core.jobs import enqueue as enqueue_mod
from prodavan.core.jobs import names as job_names
from prodavan.core.jobs.idempotency import dispatch_trigger_task_id, rematerialize_project_task_id


def test_enqueue_dispatch_trigger_passes_task_id(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict] = []

    class _Mgr:
        enabled = True

        def send_task(self, name, args=None, kwargs=None, *, task_id=None):
            sent.append({"name": name, "args": args, "task_id": task_id})

    monkeypatch.setattr(
        "prodavan.core.infra.worker_manager.get_worker_manager",
        lambda: _Mgr(),
    )
    out = enqueue_mod.enqueue_dispatch_trigger("t-9")
    assert out["enqueued"] is True
    assert out["task_id"] == dispatch_trigger_task_id("t-9")
    assert sent == [
        {
            "name": job_names.DISPATCH_TRIGGER,
            "args": ["t-9"],
            "task_id": dispatch_trigger_task_id("t-9"),
        }
    ]


def test_enqueue_rematerialize_passes_task_id(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict] = []

    class _Mgr:
        enabled = True

        def send_task(self, name, args=None, kwargs=None, *, task_id=None):
            sent.append({"name": name, "args": args, "task_id": task_id})

    monkeypatch.setattr(
        "prodavan.core.infra.worker_manager.get_worker_manager",
        lambda: _Mgr(),
    )
    out = enqueue_mod.enqueue_rematerialize_project("proj-a")
    assert out["task_id"] == rematerialize_project_task_id("proj-a")
    assert sent[0]["task_id"] == rematerialize_project_task_id("proj-a")


def test_enqueue_dispatch_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "prodavan.core.infra.worker_manager.get_worker_manager",
        lambda: SimpleNamespace(enabled=False),
    )
    out = enqueue_mod.enqueue_dispatch_trigger("t-1")
    assert out["enqueued"] is False
    assert out["reason"] == "celery_disabled"


def test_enqueue_wipe_cabinet_packages_passes_task_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from prodavan.core.jobs.idempotency import wipe_cabinet_packages_task_id

    sent: list[dict] = []

    class _Mgr:
        enabled = True

        def send_task(self, name, args=None, kwargs=None, *, task_id=None):
            sent.append({"name": name, "args": args, "task_id": task_id})

    monkeypatch.setattr(
        "prodavan.core.infra.worker_manager.get_worker_manager",
        lambda: _Mgr(),
    )
    out = enqueue_mod.enqueue_wipe_cabinet_packages("cab-1")
    assert out["task_id"] == wipe_cabinet_packages_task_id("cab-1")
    assert sent[0]["name"] == job_names.WIPE_CABINET_PACKAGES
