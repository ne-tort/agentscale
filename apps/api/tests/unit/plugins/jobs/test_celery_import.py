"""Import regression for the real Celery queue (not executed during freeze)."""
from __future__ import annotations

from typing import Any

from prodavan.application.plugins.capabilities.contracts import empty_digest
from prodavan.infrastructure.plugins.capabilities.celery import RedisCeleryJobQueue


class Redis:
    async def get(self, key: str) -> Any:
        return None

    async def set(self, key: str, value: Any, **kwargs: Any) -> bool:
        return True


def test_real_queue_imports_and_initial_result_is_canonical_empty_digest() -> None:
    queue = RedisCeleryJobQueue(
        redis=Redis(),
        worker_provider=lambda: object(),
        celery_provider=lambda: object(),
        clock=lambda: 1000.0,
    )
    record = queue._new_record({
        "company_id": "co_123",
        "plugin_id": "mod_x",
        "plugin_instance_id": "inst_abc",
        "grant_id": "grant_01",
        "task_name": "plugin.safe.task",
        "run_id": "run1",
        "arguments_digest": empty_digest(),
        "job_id": "job_import_regression",
    })
    assert record["state"] == "pending"
    assert record["result_digest"] == empty_digest()
    assert record["submission_uncertain"] is False
