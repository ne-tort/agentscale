"""Unit tests for RuntimeObservationService FSM."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.pod_service.runtime_observation import RuntimeObservationService
from prodavan.core.events.envelope import EventEnvelope
from prodavan.domain.pods import PodDesiredState, PodStatus
from prodavan.domain.pods.observed_state import ObservedState
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow


def _project(**kwargs) -> ProjectRow:
    row = ProjectRow(
        id="proj_test",
        company_id="co_test",
        cabinet_id="cab_test",
        name="Test",
        slug="test",
        status="active",
        visibility_mode="cabinet_shared",
        workspace_key="wk_test",
        container_ref="object-ws:wk_test",
    )
    for k, v in kwargs.items():
        setattr(row, k, v)
    return row


def _pod(**kwargs) -> ProjectPodRow:
    row = ProjectPodRow(
        id="pod_test",
        project_id="proj_test",
        workspace_key="wk_test",
        status=PodStatus.PROVISIONING,
        desired_state=PodDesiredState.RUNNING.value,
        runtime_ref="pod-wk-test",
    )
    row.updated_at = datetime.now(UTC) - timedelta(seconds=5)
    for k, v in kwargs.items():
        setattr(row, k, v)
    return row


def test_metrics_verified_requires_fresh_cpu_mem() -> None:
    ts = EventEnvelope.now_iso()
    assert RuntimeObservationService._metrics_verified(
        {"cpu_millicores": 10, "memory_bytes": 1024, "timestamp": ts},
        None,
    )
    assert not RuntimeObservationService._metrics_verified(
        {"cpu_millicores": 10, "memory_bytes": 1024, "timestamp": "1999-01-01T00:00:00+00:00"},
        None,
    )
    assert not RuntimeObservationService._metrics_verified(None, {"degraded": True})


@pytest.mark.asyncio
async def test_observe_preparing_from_launch_phase() -> None:
    session = AsyncMock()
    svc = RuntimeObservationService(session)
    project = _project(launch_phase="preparing")
    out = await svc.observe(project=project, pod=None)
    assert out["observed_state"] == ObservedState.PREPARING.value


@pytest.mark.asyncio
async def test_observe_stub_running_with_verified_metrics() -> None:
    session = AsyncMock()
    svc = RuntimeObservationService(session)
    project = _project()
    pod = _pod(status=PodStatus.PROVISIONING)
    ts = EventEnvelope.now_iso()
    svc._metrics_query.get_project_runtime_metrics = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "cpu_millicores": 5,
            "memory_bytes": 4096,
            "timestamp": ts,
            "phase": "Running",
            "ready": True,
        }
    )
    with patch("prodavan.application.pod_service.runtime_observation.settings") as mock_settings:
        mock_settings.pod_runtime_mode = "stub"
        out = await svc.observe(project=project, pod=pod)
    assert out["observed_state"] == ObservedState.RUNNING.value
    assert out.get("metrics_fresh") is True


@pytest.mark.asyncio
async def test_observe_k8s_starting_within_grace() -> None:
    session = AsyncMock()
    svc = RuntimeObservationService(session)
    project = _project()
    pod = _pod(status=PodStatus.PROVISIONING)
    svc._metrics_query.get_project_runtime_metrics = AsyncMock(return_value=None)  # type: ignore[method-assign]

    runtime_mock = AsyncMock()
    runtime_mock.get_status = AsyncMock(
        return_value={"phase": "Running", "ready": True, "restarts": 0, "stub": False}
    )
    metrics_port = AsyncMock()
    metrics_port.get_pod_metrics = AsyncMock(return_value=None)

    with patch("prodavan.application.pod_service.runtime_observation.settings") as mock_settings:
        mock_settings.pod_runtime_mode = "k8s"
        mock_settings.pod_provisioning_timeout_sec = 300
        mock_settings.pod_metrics_grace_sec = 90
        with patch("prodavan.application.pod_service.runtime_observation.build_pod_runtime", return_value=runtime_mock):
            with patch(
                "prodavan.application.pod_service.runtime_observation.build_pod_metrics",
                return_value=metrics_port,
            ):
                out = await svc.observe(project=project, pod=pod)
    assert out["observed_state"] == ObservedState.STARTING.value


@pytest.mark.asyncio
async def test_observe_k8s_running_with_fresh_metrics() -> None:
    session = AsyncMock()
    svc = RuntimeObservationService(session)
    project = _project()
    pod = _pod(status=PodStatus.RUNNING)
    ts = EventEnvelope.now_iso()
    svc._metrics_query.get_project_runtime_metrics = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "cpu_millicores": 50,
            "memory_bytes": 65536,
            "timestamp": ts,
            "phase": "Running",
            "ready": True,
        }
    )

    with patch("prodavan.application.pod_service.runtime_observation.settings") as mock_settings:
        mock_settings.pod_runtime_mode = "k8s"
        mock_settings.pod_provisioning_timeout_sec = 300
        mock_settings.pod_metrics_grace_sec = 90
        mock_settings.metrics_sample_ttl_sec = 900
        out = await svc.observe(project=project, pod=pod)
    assert out["observed_state"] == ObservedState.RUNNING.value


@pytest.mark.asyncio
async def test_promote_provisioning_to_running() -> None:
    session = MagicMock()
    svc = RuntimeObservationService(session)
    project = _project()
    pod = _pod(status=PodStatus.PROVISIONING)
    svc.observe = AsyncMock(  # type: ignore[method-assign]
        return_value={"observed_state": ObservedState.RUNNING.value}
    )
    action = await svc.promote_or_demote(project=project, pod=pod)
    assert action == "promoted"
    assert pod.status == PodStatus.RUNNING
