"""Unit tests for PodQuery."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.pod_service.query import PodQuery
from prodavan.domain.pods import PodDesiredState, PodStatus
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow


@pytest.mark.asyncio
async def test_get_for_project_returns_single_live_pod() -> None:
    session = AsyncMock()
    pod = ProjectPodRow(
        id="pod_live123",
        project_id="prj_test1234567890",
        workspace_key="wk_demo",
        status=PodStatus.RUNNING,
        desired_state=PodDesiredState.RUNNING,
        runtime_ref="object-ws:wk_demo",
        hydrate_generation=0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = pod
    session.execute = AsyncMock(return_value=execute_result)

    out = await PodQuery(session).get_for_project("prj_test1234567890")

    assert out is not None
    assert out["id"] == "pod_live123"
    assert out["status"] == PodStatus.RUNNING


@pytest.mark.asyncio
async def test_runtime_summary_shape() -> None:
    session = AsyncMock()
    pod = ProjectPodRow(
        id="pod_live123",
        project_id="prj_test1234567890",
        workspace_key="wk_demo",
        status=PodStatus.PAUSED,
        desired_state=PodDesiredState.ABSENT,
        runtime_ref="object-ws:wk_demo",
        hydrate_generation=0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = pod
    session.execute = AsyncMock(return_value=execute_result)

    summary = await PodQuery(session).runtime_summary("prj_test1234567890")

    assert summary is not None
    assert summary["pod_id"] == "pod_live123"
    assert summary["status"] == PodStatus.PAUSED
    assert summary["observed_state"] == "paused"
    assert summary["desired_state"] == PodDesiredState.ABSENT


@pytest.mark.asyncio
async def test_runtime_view_includes_failed_pod() -> None:
    """FAILED + desired=RUNNING re-observes; stub mode must keep last_error."""
    session = AsyncMock()
    failed = ProjectPodRow(
        id="pod_failed",
        project_id="prj_test1234567890",
        workspace_key="wk_demo",
        status=PodStatus.FAILED,
        desired_state=PodDesiredState.RUNNING,
        runtime_ref="object-ws:wk_demo",
        last_error="boom",
        hydrate_generation=0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    project = MagicMock()
    project.launch_phase = None
    project.status = ProjectStatus.ERROR
    live_result = MagicMock()
    live_result.scalar_one_or_none.return_value = None
    failed_result = MagicMock()
    failed_result.scalar_one_or_none.return_value = failed
    session.execute = AsyncMock(side_effect=[live_result, failed_result])
    session.get = AsyncMock(return_value=project)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    # runtime_observation imports settings at module level — patch there, not only
    # prodavan.config.settings (FAILED+RUNNING no longer short-circuits before mode).
    with (
        patch("prodavan.application.pod_service.runtime_observation.settings") as obs_settings,
        patch("prodavan.config.settings.settings") as cfg_settings,
    ):
        for mock_settings in (obs_settings, cfg_settings):
            mock_settings.pod_runtime_mode = "stub"
            mock_settings.pod_provisioning_timeout_sec = 300
        summary = await PodQuery(session).runtime_view("prj_test1234567890")

    assert summary is not None
    assert summary["status"] == PodStatus.FAILED
    assert summary["last_error"] == "boom"


@pytest.mark.asyncio
async def test_runtime_view_observes_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """B4: runtime_view pays for ONE k8s observation — sync_runtime_health
    and the summary builder share the same obs dict."""
    from prodavan.application.pod_service.runtime_observation import RuntimeObservationService
    from prodavan.config.settings import settings as real_settings

    monkeypatch.setattr(real_settings, "pod_runtime_mode", "stub")

    session = AsyncMock()
    pod = ProjectPodRow(
        id="pod_live123",
        project_id="prj_test1234567890",
        workspace_key="wk_demo",
        status=PodStatus.RUNNING,
        desired_state=PodDesiredState.RUNNING,
        runtime_ref="object-ws:wk_demo",
        hydrate_generation=0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = pod
    session.execute = AsyncMock(return_value=execute_result)
    project = MagicMock()
    project.launch_phase = None
    project.status = ProjectStatus.ACTIVE
    session.get = AsyncMock(return_value=project)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    obs_payload = {
        "observed_state": "running",
        "orchestrator_status": PodStatus.RUNNING,
        "desired_state": PodDesiredState.RUNNING.value,
        "stub": True,
        "metrics_fresh": False,
    }
    observe = AsyncMock(return_value=dict(obs_payload))
    monkeypatch.setattr(RuntimeObservationService, "observe", observe)

    summary = await PodQuery(session).runtime_view("prj_test1234567890")

    assert summary is not None
    assert summary["observed_state"] == "running"
    assert observe.await_count == 1

