"""Unit tests for PodReconcileService zombie reaper."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.pod_service.reconcile import PodReconcileService
from prodavan.config.settings import settings


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
    # Older than pull+ready grace (image pull budget + ready + slack).
    grace = settings.pod_image_pull_timeout_sec + settings.pod_ready_timeout_sec + 40
    old = (
        datetime.now(UTC) - timedelta(seconds=grace + 60)
    ).isoformat().replace("+00:00", "Z")
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


@pytest.mark.asyncio
async def test_run_skips_when_advisory_lock_held() -> None:
    # POD-P2a: when another process holds the reconcile lock, run() skips
    # without touching k8s or DB rows.
    session = AsyncMock()
    lock_result = MagicMock()
    lock_result.scalar.return_value = False  # pg_try_advisory_lock → False
    session.execute = AsyncMock(return_value=lock_result)

    svc = PodReconcileService(session, runtime=AsyncMock())
    result = await svc.run()

    assert result == {"skipped": True, "reason": "lock_held"}
    # No reconcile queries after the lock probe (execute called once for lock).
    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_run_reconciles_when_advisory_lock_acquired() -> None:
    # When the lock is acquired, run() delegates to _run_reconcile. We verify
    # the lock is acquired+released (2 executes) and _run_reconcile is called.
    session = AsyncMock()
    lock_acquired = MagicMock()
    lock_acquired.scalar.return_value = True  # pg_try_advisory_lock → True
    session.execute = AsyncMock(return_value=lock_acquired)
    session.commit = AsyncMock()

    svc = PodReconcileService(session, runtime=AsyncMock())
    called: dict = {}

    async def _fake_run() -> dict:
        called["run"] = True
        return {"fixed": 0, "zombies_deleted": 0, "metrics": {}, "observed": 0}

    svc._run_reconcile = _fake_run  # type: ignore[method-assign]
    result = await svc.run()

    assert called.get("run") is True
    assert result["fixed"] == 0
    # Lock acquired + released.
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_reap_zombies_skips_pod_without_created_at(caplog) -> None:
    # H3: without created_at the provisioning grace cannot be applied — the
    # reaper must NOT delete (it could kill a just-created sandbox).
    session = AsyncMock()
    runtime = AsyncMock()
    runtime.list_managed_pods = AsyncMock(
        return_value=[{"runtime_ref": "pod-wk-orphan", "pod_id": "pod_orphan"}]
    )
    runtime.terminate = AsyncMock()

    live_q = MagicMock()
    live_q.all.return_value = []
    session.execute = AsyncMock(return_value=live_q)

    svc = PodReconcileService(session, runtime=runtime)
    with caplog.at_level("WARNING"):
        deleted = await svc._reap_zombies()

    assert deleted == 0
    runtime.terminate.assert_not_awaited()
    assert any("created_at" in str(r.getMessage()) for r in caplog.records)
