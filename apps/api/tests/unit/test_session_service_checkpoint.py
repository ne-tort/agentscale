"""Unit tests — post-turn workspace checkpoint throttle (B9).

The checkpoint streams + uploads the whole workspace; one per project per
``pod_workspace_checkpoint_interval_sec`` is enough for the last-good tree.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.agent import session_service as ss_mod
from prodavan.application.agent.session_service import AgentSessionService
from prodavan.config.settings import settings


@pytest.mark.asyncio
async def test_checkpoint_throttled_within_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    """Second call inside the window is skipped (Redis NX guard denies it)."""
    monkeypatch.setattr(settings, "pod_workspace_checkpoint_interval_sec", 300)
    checkpoint = AsyncMock()
    monkeypatch.setattr(ss_mod, "checkpoint_project_workspace", checkpoint)
    # First call wins the NX guard, the second inside the window does not.
    acquire = AsyncMock(side_effect=["tok-1", None])
    monkeypatch.setattr("prodavan.core.infra.cache.acquire_lock", acquire)
    mgr = MagicMock()
    mgr.enabled = True
    monkeypatch.setattr(
        "prodavan.core.infra.redis_manager.get_redis_manager",
        lambda: mgr,
    )

    svc = AgentSessionService(MagicMock())
    await svc._checkpoint_workspace_after_turn(project_id="prj_1")
    await svc._checkpoint_workspace_after_turn(project_id="prj_1")

    assert checkpoint.await_count == 1
    assert acquire.await_count == 2
    assert acquire.await_args_list[0].kwargs["ttl_sec"] == 300


@pytest.mark.asyncio
async def test_checkpoint_runs_unthrottled_without_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No Redis → no throttle (the checkpoint itself is best-effort anyway)."""
    monkeypatch.setattr(settings, "pod_workspace_checkpoint_interval_sec", 300)
    checkpoint = AsyncMock()
    monkeypatch.setattr(ss_mod, "checkpoint_project_workspace", checkpoint)
    monkeypatch.setattr(
        "prodavan.core.infra.redis_manager.get_redis_manager",
        lambda: None,
    )

    svc = AgentSessionService(MagicMock())
    await svc._checkpoint_workspace_after_turn(project_id="prj_1")
    await svc._checkpoint_workspace_after_turn(project_id="prj_1")

    assert checkpoint.await_count == 2


@pytest.mark.asyncio
async def test_checkpoint_throttle_disabled_at_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """interval=0 disables the throttle entirely (no Redis touch)."""
    monkeypatch.setattr(settings, "pod_workspace_checkpoint_interval_sec", 0)
    checkpoint = AsyncMock()
    monkeypatch.setattr(ss_mod, "checkpoint_project_workspace", checkpoint)
    acquire = AsyncMock()
    monkeypatch.setattr("prodavan.core.infra.cache.acquire_lock", acquire)

    svc = AgentSessionService(MagicMock())
    await svc._checkpoint_workspace_after_turn(project_id="prj_1")

    checkpoint.assert_awaited_once()
    acquire.assert_not_awaited()
