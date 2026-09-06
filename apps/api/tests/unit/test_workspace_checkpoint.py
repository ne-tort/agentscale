"""Unit tests — checkpoint_project_workspace orchestration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from prodavan.application.pod_service.ports.dehydrate import DehydrateResult
from prodavan.application.projects.workspace_checkpoint import checkpoint_project_workspace
from prodavan.domain.pods import PodStatus


@pytest.mark.asyncio
async def test_checkpoint_skips_without_pod() -> None:
    session = AsyncMock()
    session.get = AsyncMock(return_value=SimpleNamespace(id="p1", workspace_key="ws", container_ref=None))
    session.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))
    out = await checkpoint_project_workspace(session, project_id="p1")
    assert out is None


@pytest.mark.asyncio
async def test_checkpoint_calls_dehydrate() -> None:
    session = AsyncMock()
    session.get = AsyncMock(
        return_value=SimpleNamespace(id="p1", workspace_key="ws1", container_ref="pod-a")
    )
    pod = SimpleNamespace(
        runtime_ref="pod-a",
        workspace_key="ws1",
        status=PodStatus.RUNNING,
    )
    session.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: pod))
    dehydrate = SimpleNamespace(
        dehydrate=AsyncMock(return_value=DehydrateResult(uploaded=3, deleted=1))
    )
    out = await checkpoint_project_workspace(
        session, project_id="p1", dehydrate=dehydrate, best_effort=False
    )
    assert out is not None
    assert out.uploaded == 3
    dehydrate.dehydrate.assert_awaited_once_with(workspace_key="ws1", runtime_ref="pod-a")


@pytest.mark.asyncio
async def test_checkpoint_best_effort_swallows_errors() -> None:
    session = AsyncMock()
    session.get = AsyncMock(
        return_value=SimpleNamespace(id="p1", workspace_key="ws1", container_ref="pod-a")
    )
    pod = SimpleNamespace(runtime_ref="pod-a", workspace_key="ws1", status=PodStatus.RUNNING)
    session.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: pod))
    dehydrate = SimpleNamespace(dehydrate=AsyncMock(side_effect=RuntimeError("boom")))
    out = await checkpoint_project_workspace(
        session, project_id="p1", dehydrate=dehydrate, best_effort=True
    )
    assert out is None
