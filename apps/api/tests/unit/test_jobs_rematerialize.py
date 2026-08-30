"""Unit tests — background rematerialize bumps hydrate_generation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from prodavan.application.project_service.command import ProjectCommand
from prodavan.config.settings import settings
from prodavan.domain.projects import ProjectStatus


@pytest.fixture(autouse=True)
def _stub_pod_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "pod_runtime_mode", "stub")


@pytest.mark.asyncio
async def test_rematerialize_background_bumps_hydrate_for_live_pod(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()
    cmd = ProjectCommand(session)
    row = SimpleNamespace(
        id="proj_1",
        cabinet_id="cab_1",
        status=ProjectStatus.ACTIVE,
    )
    live_pod = SimpleNamespace(hydrate_generation=0)

    async def _get_project(_pid: str):
        return row

    async def _get_live(_pid: str):
        return live_pod

    mat = SimpleNamespace(
        workspace_root="/ws",
        mcp_config_path="/ws/mcp.json",
        status="materialized",
        package_names=(),
    )

    monkeypatch.setattr(cmd._access, "get_project_or_none", _get_project)
    monkeypatch.setattr(cmd, "_get_live_pod", _get_live)
    monkeypatch.setattr(cmd, "_sync_project_workspace", AsyncMock(return_value=mat))
    sync_desired = AsyncMock()
    monkeypatch.setattr(cmd._pods, "sync_desired", sync_desired)

    out = await cmd.rematerialize_background(project_id="proj_1")

    assert out["ok"] is True
    assert out["hydrate_generation"] == 1
    assert live_pod.hydrate_generation == 1
    sync_desired.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_rematerialize_background_skips_hydrate_without_pod(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()
    cmd = ProjectCommand(session)
    row = SimpleNamespace(id="proj_2", cabinet_id="cab_1", status=ProjectStatus.DRAFT)
    mat = SimpleNamespace(
        workspace_root="/ws",
        mcp_config_path="/ws/mcp.json",
        status="materialized",
        package_names=(),
    )

    monkeypatch.setattr(cmd._access, "get_project_or_none", AsyncMock(return_value=row))
    monkeypatch.setattr(cmd, "_get_live_pod", AsyncMock(return_value=None))
    monkeypatch.setattr(cmd, "_sync_project_workspace", AsyncMock(return_value=mat))
    sync_desired = AsyncMock()
    monkeypatch.setattr(cmd._pods, "sync_desired", sync_desired)

    out = await cmd.rematerialize_background(project_id="proj_2")

    assert out["ok"] is True
    assert "hydrate_generation" not in out
    sync_desired.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_runtime_system_delegates_to_pods(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()
    cmd = ProjectCommand(session)
    row = SimpleNamespace(id="proj_3")
    stop = AsyncMock()
    monkeypatch.setattr(cmd._access, "get_project_or_none", AsyncMock(return_value=row))
    monkeypatch.setattr(cmd, "_stop_and_pause_runtime", stop)

    await cmd.stop_runtime_system(project_id="proj_3", reason="purge")

    stop.assert_awaited_once()
    assert stop.await_args.kwargs["reason"] == "purge"


@pytest.mark.asyncio
async def test_rematerialize_background_skips_deleted_project(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()
    cmd = ProjectCommand(session)
    row = SimpleNamespace(id="proj_del", cabinet_id="cab_1", status=ProjectStatus.DELETED)
    monkeypatch.setattr(cmd._access, "get_project_or_none", AsyncMock(return_value=row))
    sync = AsyncMock()
    monkeypatch.setattr(cmd, "_sync_project_workspace", sync)

    out = await cmd.rematerialize_background(project_id="proj_del")

    assert out["ok"] is False
    assert out["reason"] == "deleted"
    sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_rematerialize_background_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()
    cmd = ProjectCommand(session)
    monkeypatch.setattr(cmd._access, "get_project_or_none", AsyncMock(return_value=None))

    out = await cmd.rematerialize_background(project_id="missing")

    assert out == {"ok": False, "reason": "not_found", "project_id": "missing"}
