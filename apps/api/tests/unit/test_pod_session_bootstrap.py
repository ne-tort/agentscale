"""Unit tests — pod session bootstrap after runtime restart."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.agent.pod_session_bootstrap import PodSessionBootstrap
from prodavan.domain.agent import AgentSessionStatus
from prodavan.infrastructure.persistence.models.agent import AgentSessionRow
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow


@pytest.mark.asyncio
async def test_bootstrap_registers_active_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "prodavan.application.agent.pod_session_bootstrap.settings.pod_agent_runtime_enabled",
        True,
    )
    session = AsyncMock()
    pod_row = ProjectPodRow(
        id="pod_1",
        project_id="proj_1",
        workspace_key="ws_1",
        status="running",
        desired_state="running",
        hydrate_generation=2,
    )
    agent_row = AgentSessionRow(
        id="ags_1",
        project_id="proj_1",
        resolved_key_id="key_1",
        provider="cursor",
        api_kind="cursor_sdk",
        vendor_agent_id="ags_1",
        model=None,
        cwd="/workspace",
        status=AgentSessionStatus.ACTIVE,
    )

    pod_result = MagicMock()
    pod_result.scalar_one_or_none.return_value = pod_row
    rows_result = MagicMock()
    rows_result.scalars.return_value.all.return_value = [agent_row]

    session.execute = AsyncMock(side_effect=[pod_result, rows_result])
    session.commit = AsyncMock()

    bootstrap = PodSessionBootstrap(session)
    bootstrap._sessions.reactivate_resumable_for_project = AsyncMock(return_value=["ags_1"])  # type: ignore[method-assign]
    bootstrap._bridge.register_session = AsyncMock(return_value=True)  # type: ignore[method-assign]

    with patch(
        "prodavan.application.agent.pod_session_bootstrap.acquire_lock",
        AsyncMock(return_value="tok"),
    ), patch(
        "prodavan.application.agent.pod_session_bootstrap.release_lock",
        AsyncMock(return_value=True),
    ), patch(
        "prodavan.application.agent.pod_session_bootstrap._redis_enabled",
        return_value=True,
    ), patch(
        "prodavan.application.agent.pod_session_bootstrap.RuntimeObservationService"
    ) as obs_cls, patch(
        "prodavan.application.agent.pod_session_bootstrap.AgentCredentialBroker"
    ) as broker_cls:
        obs_cls.return_value.wait_for_running = AsyncMock(return_value={"observed_state": "running"})
        broker_cls.return_value.push_lease_to_runtime = AsyncMock(return_value=True)
        out = await bootstrap.bootstrap_project_sessions(project_id="proj_1", wait_for_pod=False)

    assert out["registered"] == 1
    assert out["reactivated"] == 1
    bootstrap._bridge.register_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_bootstrap_releases_lock_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """B6: an exception mid-bootstrap must release the guard lock — otherwise
    the guard strands for its 300s TTL and silently skips re-registration."""
    monkeypatch.setattr(
        "prodavan.application.agent.pod_session_bootstrap.settings.pod_agent_runtime_enabled",
        True,
    )
    session = AsyncMock()
    pod_row = ProjectPodRow(
        id="pod_1",
        project_id="proj_1",
        workspace_key="ws_1",
        status="running",
        desired_state="running",
        hydrate_generation=2,
    )
    pod_result = MagicMock()
    pod_result.scalar_one_or_none.return_value = pod_row
    session.execute = AsyncMock(return_value=pod_result)

    bootstrap = PodSessionBootstrap(session)
    bootstrap._sessions.reactivate_resumable_for_project = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("db blew up")
    )

    release = AsyncMock(return_value=True)
    with (
        patch(
            "prodavan.application.agent.pod_session_bootstrap.acquire_lock",
            AsyncMock(return_value="tok"),
        ),
        patch(
            "prodavan.application.agent.pod_session_bootstrap.release_lock",
            release,
        ),
        patch(
            "prodavan.application.agent.pod_session_bootstrap._redis_enabled",
            return_value=True,
        ),
    ):
        with pytest.raises(RuntimeError, match="db blew up"):
            await bootstrap.bootstrap_project_sessions(project_id="proj_1", wait_for_pod=False)

    release.assert_awaited_once()
    assert release.await_args.args[1] == "tok"
