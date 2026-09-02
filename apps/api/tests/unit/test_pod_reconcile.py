"""Unit tests for PodReconcileService zombie reaper."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.pod_service.reconcile import PodReconcileService


@pytest.mark.asyncio
async def test_reap_zombies_skips_young_managed_pod() -> None:
    session = AsyncMock()
    runtime = AsyncMock()
    runtime.list_managed_pods = AsyncMock(
        return_value=[
            {
                "runtime_ref": "pod-wk-demo",
                "pod_id": "pod_abc123",
                "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
        ]
    )
    runtime.terminate = AsyncMock()

    live_q = MagicMock()
    live_q.all.return_value = []
    session.execute = AsyncMock(return_value=live_q)

    svc = PodReconcileService(session, runtime=runtime)
    deleted = await svc._reap_zombies()

    assert deleted == 0
    runtime.terminate.assert_not_awaited()


@pytest.mark.asyncio
async def test_reap_zombies_deletes_stale_untracked_pod() -> None:
    session = AsyncMock()
    runtime = AsyncMock()
    old = (datetime.now(UTC) - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    runtime.list_managed_pods = AsyncMock(
        return_value=[
            {
                "runtime_ref": "pod-wk-stale",
                "pod_id": "pod_stale123",
                "created_at": old,
            }
        ]
    )
    runtime.terminate = AsyncMock()

    live_q = MagicMock()
    live_q.all.return_value = []
    session.execute = AsyncMock(return_value=live_q)

    svc = PodReconcileService(session, runtime=runtime)
    deleted = await svc._reap_zombies()

    assert deleted == 1
    runtime.terminate.assert_awaited_once_with(runtime_ref="pod-wk-stale")
