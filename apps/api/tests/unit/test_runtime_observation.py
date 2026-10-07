"""Unit tests for RuntimeObservationService FSM."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.pod_service.runtime_observation import RuntimeObservationService
from prodavan.config.settings import settings
from prodavan.core.events.envelope import EventEnvelope
from prodavan.domain.pods import PodDesiredState, PodStatus
from prodavan.domain.pods.observed_state import ObservedState
from prodavan.domain.projects import ProjectStatus
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


def test_metrics_available_requires_fresh_cpu_mem() -> None:
    ts = EventEnvelope.now_iso()
    assert RuntimeObservationService._metrics_available(
        {"cpu_millicores": 10, "memory_bytes": 1024, "timestamp": ts},
        None,
    )
    assert not RuntimeObservationService._metrics_available(
        {"cpu_millicores": 10, "memory_bytes": 1024, "timestamp": "1999-01-01T00:00:00+00:00"},
        None,
    )
    assert not RuntimeObservationService._metrics_available(None, {"degraded": True})


def test_project_is_recoverable_failed_only() -> None:
    from prodavan.application.pod_service.runtime_observation import project_is_recoverable

    project = _project()
    pod = _pod(status=PodStatus.RUNNING)
    assert not project_is_recoverable(
        project,
        pod,
        {"observed_state": ObservedState.RUNNING.value, "metrics_available": False},
    )
    assert project_is_recoverable(
        project,
        pod,
        {"observed_state": ObservedState.FAILED.value},
    )
    assert not project_is_recoverable(project, pod, {"observed_state": ObservedState.RUNNING.value})


def test_project_is_recoverable_error_status() -> None:
    from prodavan.application.pod_service.runtime_observation import project_is_recoverable
    from prodavan.domain.projects import ProjectStatus

    project = _project()
    project.status = ProjectStatus.ERROR
    assert project_is_recoverable(project, None, None)


@pytest.mark.asyncio
async def test_observe_preparing_from_launch_phase() -> None:
    session = AsyncMock()
    svc = RuntimeObservationService(session)
    project = _project(launch_phase="preparing")
    out = await svc.observe(project=project, pod=None)
    assert out["observed_state"] == ObservedState.PREPARING.value


@pytest.mark.asyncio
async def test_observe_stub_running_without_metrics() -> None:
    session = AsyncMock()
    svc = RuntimeObservationService(session)
    project = _project()
    pod = _pod(status=PodStatus.RUNNING)
    with patch("prodavan.application.pod_service.runtime_observation.settings") as mock_settings:
        mock_settings.pod_runtime_mode = "stub"
        mock_settings.pod_provisioning_timeout_sec = 300
        out = await svc.observe(project=project, pod=pod)
    assert out["observed_state"] == ObservedState.RUNNING.value
    assert out.get("stub") is True
    assert "metrics" not in out


@pytest.mark.asyncio
async def test_observe_stub_failed() -> None:
    session = AsyncMock()
    svc = RuntimeObservationService(session)
    project = _project()
    pod = _pod(status=PodStatus.FAILED, last_error="boom")
    with patch("prodavan.application.pod_service.runtime_observation.settings") as mock_settings:
        mock_settings.pod_runtime_mode = "stub"
        out = await svc.observe(project=project, pod=pod)
    assert out["observed_state"] == ObservedState.FAILED.value
    assert out.get("last_error") == "boom"


@pytest.mark.asyncio
async def test_observe_stub_provisioning_timeout() -> None:
    session = AsyncMock()
    svc = RuntimeObservationService(session)
    project = _project()
    pod = _pod(status=PodStatus.PROVISIONING)
    pod.updated_at = datetime.now(UTC) - timedelta(seconds=400)
    with patch("prodavan.application.pod_service.runtime_observation.settings") as mock_settings:
        mock_settings.pod_runtime_mode = "stub"
        mock_settings.pod_provisioning_timeout_sec = 300
        out = await svc.observe(project=project, pod=pod)
    assert out["observed_state"] == ObservedState.FAILED.value
    assert out.get("last_error")
    assert out.get("stub") is True


@pytest.mark.asyncio
async def test_promote_or_demote_stub_provisioning_timeout() -> None:
    session = MagicMock()
    svc = RuntimeObservationService(session)
    project = _project()
    pod = _pod(status=PodStatus.PROVISIONING)
    pod.updated_at = datetime.now(UTC) - timedelta(seconds=400)
    svc.observe = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "observed_state": ObservedState.FAILED.value,
            "last_error": "pod runtime unavailable",
        }
    )
    with patch("prodavan.application.pod_service.runtime_observation.settings") as mock_settings:
        mock_settings.pod_runtime_mode = "stub"
        mock_settings.pod_provisioning_timeout_sec = 300
        action = await svc.promote_or_demote(project=project, pod=pod)
    assert action == "failed_timeout"
    assert pod.status == PodStatus.FAILED
    assert project.status == ProjectStatus.ERROR


@pytest.mark.asyncio
async def test_promote_or_demote_stub_promotes_provisioning() -> None:
    session = MagicMock()
    svc = RuntimeObservationService(session)
    project = _project()
    pod = _pod(status=PodStatus.PROVISIONING)
    svc.observe = AsyncMock(  # type: ignore[method-assign]
        return_value={"observed_state": ObservedState.STARTING.value},
    )
    with patch("prodavan.application.pod_service.runtime_observation.settings") as mock_settings:
        mock_settings.pod_runtime_mode = "stub"
        mock_settings.pod_provisioning_timeout_sec = 300
        action = await svc.promote_or_demote(project=project, pod=pod)
    assert action == "promoted"
    assert pod.status == PodStatus.RUNNING
    assert pod.last_error is None


@pytest.mark.asyncio
async def test_promote_or_demote_k8s_unknown_with_error() -> None:
    session = MagicMock()
    svc = RuntimeObservationService(session)
    project = _project()
    pod = _pod(status=PodStatus.RUNNING)
    pod.updated_at = datetime.now(UTC) - timedelta(seconds=400)
    svc.observe = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "observed_state": ObservedState.UNKNOWN.value,
            "last_error": "metrics not verified",
        }
    )
    with patch("prodavan.application.pod_service.runtime_observation.settings") as mock_settings:
        mock_settings.pod_runtime_mode = "k8s"
        mock_settings.pod_provisioning_timeout_sec = 30
        action = await svc.promote_or_demote(project=project, pod=pod)
    assert action == "demoted"
    assert pod.status == PodStatus.FAILED
    assert project.status == ProjectStatus.ERROR


@pytest.mark.asyncio
async def test_promote_or_demote_k8s_absent_within_flap_grace() -> None:
    session = MagicMock()
    svc = RuntimeObservationService(session)
    project = _project()
    pod = _pod(status=PodStatus.RUNNING)
    pod.updated_at = datetime.now(UTC) - timedelta(seconds=5)
    svc.observe = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "observed_state": ObservedState.ABSENT.value,
            "last_error": "pod not found in k8s",
        }
    )
    with patch("prodavan.application.pod_service.runtime_observation.settings") as mock_settings:
        mock_settings.pod_runtime_mode = "k8s"
        mock_settings.pod_provisioning_timeout_sec = 30
        action = await svc.promote_or_demote(project=project, pod=pod)
    assert action == "noop"
    assert pod.status == PodStatus.RUNNING
    assert project.status == ProjectStatus.ACTIVE


@pytest.mark.asyncio
async def test_promote_or_demote_recovers_failed_when_running() -> None:
    session = MagicMock()
    svc = RuntimeObservationService(session)
    project = _project(status=ProjectStatus.ERROR)
    pod = _pod(status=PodStatus.FAILED, last_error="node flap")
    svc.observe = AsyncMock(  # type: ignore[method-assign]
        return_value={"observed_state": ObservedState.RUNNING.value},
    )
    with patch("prodavan.application.pod_service.runtime_observation.settings") as mock_settings:
        mock_settings.pod_runtime_mode = "k8s"
        action = await svc.promote_or_demote(project=project, pod=pod)
    assert action == "promoted"
    assert pod.status == PodStatus.RUNNING
    assert pod.last_error is None
    assert project.status == ProjectStatus.ACTIVE


@pytest.mark.asyncio
async def test_observe_k8s_pulling() -> None:
    session = AsyncMock()
    svc = RuntimeObservationService(session)
    project = _project()
    pod = _pod(status=PodStatus.PROVISIONING)
    svc._metrics_query.get_project_runtime_metrics = AsyncMock(return_value=None)  # type: ignore[method-assign]

    runtime_mock = AsyncMock()
    runtime_mock.get_status = AsyncMock(
        return_value={
            "phase": "Pending",
            "ready": False,
            "pulling": True,
            "waiting_reason": "Pulling",
            "restarts": 0,
            "stub": False,
        }
    )

    with patch("prodavan.application.pod_service.runtime_observation.settings") as mock_settings:
        mock_settings.pod_runtime_mode = "k8s"
        mock_settings.pod_provisioning_timeout_sec = 300
        mock_settings.pod_image_pull_timeout_sec = 600
        mock_settings.metrics_sample_ttl_sec = 900
        with patch(
            "prodavan.application.pod_service.runtime_observation.build_pod_runtime",
            return_value=runtime_mock,
        ):
            out = await svc.observe(project=project, pod=pod)
    assert out["observed_state"] == ObservedState.PULLING.value
    assert out.get("waiting_reason") == "Pulling"


@pytest.mark.asyncio
async def test_observe_k8s_running_without_metrics() -> None:
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
        mock_settings.metrics_sample_ttl_sec = 900
        with patch("prodavan.application.pod_service.runtime_observation.build_pod_runtime", return_value=runtime_mock):
            with patch(
                "prodavan.application.pod_service.runtime_observation.build_pod_metrics",
                return_value=metrics_port,
            ):
                out = await svc.observe(project=project, pod=pod)
    assert out["observed_state"] == ObservedState.RUNNING.value
    assert out.get("metrics_available") is False
    assert out.get("metrics_unavailable_reason")


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
async def test_observe_k8s_live_fetch_clears_cached_degraded() -> None:
    session = AsyncMock()
    svc = RuntimeObservationService(session)
    project = _project()
    pod = _pod(status=PodStatus.RUNNING)
    svc._metrics_query.get_project_runtime_metrics = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "degraded": True,
            "degraded_reason": "metrics-server unavailable",
            "phase": "Running",
            "ready": True,
        }
    )
    metrics_port = AsyncMock()
    metrics_port.get_pod_metrics = AsyncMock(
        return_value={"cpu_millicores": 80, "memory_bytes": 8192}
    )
    cache_live = AsyncMock()
    svc_sampler = MagicMock()
    svc_sampler.cache_live_metrics = cache_live

    with patch("prodavan.application.pod_service.runtime_observation.settings") as mock_settings:
        mock_settings.pod_runtime_mode = "k8s"
        mock_settings.pod_provisioning_timeout_sec = 300
        mock_settings.pod_metrics_grace_sec = 90
        mock_settings.metrics_sample_ttl_sec = 900
        with patch(
            "prodavan.application.pod_service.runtime_observation.build_pod_metrics",
            return_value=metrics_port,
        ):
            with patch(
                "prodavan.application.pod_service.runtime_observation.PodMetricsSampler",
                return_value=svc_sampler,
            ):
                out = await svc.observe(project=project, pod=pod)

    assert out["observed_state"] == ObservedState.RUNNING.value
    assert out.get("metrics_available") is True
    metrics_port.get_pod_metrics.assert_awaited_once()
    cache_live.assert_awaited_once()


@pytest.mark.asyncio
async def test_promote_or_demote_pause_safe() -> None:
    session = MagicMock()
    svc = RuntimeObservationService(session)
    project = _project()
    pod = _pod(status=PodStatus.PAUSING, desired_state=PodDesiredState.ABSENT.value)
    svc.observe = AsyncMock(  # type: ignore[method-assign]
        return_value={"observed_state": ObservedState.ABSENT.value}
    )
    with patch.object(settings, "pod_runtime_mode", "k8s"):
        action = await svc.promote_or_demote(project=project, pod=pod)
    assert action == "noop"
    assert project.status == ProjectStatus.ACTIVE


@pytest.mark.asyncio
async def test_promote_provisioning_to_running() -> None:
    session = MagicMock()
    svc = RuntimeObservationService(session)
    project = _project()
    pod = _pod(status=PodStatus.PROVISIONING)
    svc.observe = AsyncMock(  # type: ignore[method-assign]
        return_value={"observed_state": ObservedState.RUNNING.value}
    )
    with (
        patch.object(settings, "pod_runtime_mode", "k8s"),
        patch.object(settings, "pod_provisioning_timeout_sec", 300),
    ):
        action = await svc.promote_or_demote(project=project, pod=pod)
    assert action == "promoted"
    assert pod.status == PodStatus.RUNNING


# ---------------------------------------------------- wait_for_running (H1)


@pytest.mark.asyncio
async def test_wait_for_running_sandbox_polls_until_running(monkeypatch) -> None:
    monkeypatch.setattr(settings, "pod_runtime_mode", "sandbox")
    session = AsyncMock()
    project = _project()
    pod = _pod()
    svc = RuntimeObservationService(session)
    svc._get_live_pod = AsyncMock(return_value=pod)
    session.get = AsyncMock(return_value=project)
    svc.observe = AsyncMock(
        side_effect=[
            {"observed_state": ObservedState.PROVISIONING.value},
            {"observed_state": ObservedState.RUNNING.value},
        ]
    )

    out = await svc.wait_for_running(project_id="proj_test", poll_sec=0.01)

    assert out["observed_state"] == ObservedState.RUNNING.value
    assert svc.observe.await_count == 2
    assert pod.status == PodStatus.RUNNING


@pytest.mark.asyncio
async def test_wait_for_running_sandbox_times_out_not_running(monkeypatch) -> None:
    # Sandbox mode must NOT promote PROVISIONING→RUNNING instantly: until the
    # runtime reports running, the pod row stays PROVISIONING and the wait fails.
    monkeypatch.setattr(settings, "pod_runtime_mode", "sandbox")
    session = AsyncMock()
    project = _project()
    pod = _pod()
    svc = RuntimeObservationService(session)
    svc._get_live_pod = AsyncMock(return_value=pod)
    session.get = AsyncMock(return_value=project)
    svc.observe = AsyncMock(
        return_value={"observed_state": ObservedState.PROVISIONING.value}
    )

    with pytest.raises(RuntimeError):
        await svc.wait_for_running(
            project_id="proj_test", timeout_sec=0.05, poll_sec=0.02
        )
    assert pod.status == PodStatus.PROVISIONING


@pytest.mark.asyncio
async def test_wait_for_running_stub_still_promotes_instantly(monkeypatch) -> None:
    monkeypatch.setattr(settings, "pod_runtime_mode", "stub")
    session = AsyncMock()
    project = _project()
    pod = _pod()
    svc = RuntimeObservationService(session)
    svc._get_live_pod = AsyncMock(return_value=pod)
    session.get = AsyncMock(return_value=project)
    svc.observe = AsyncMock(
        return_value={"observed_state": ObservedState.PROVISIONING.value}
    )

    await svc.wait_for_running(project_id="proj_test")

    assert svc.observe.await_count == 1
    assert pod.status == PodStatus.RUNNING


@pytest.mark.asyncio
async def test_promote_or_demote_sandbox_promotes_on_observed_running() -> None:
    session = MagicMock()
    svc = RuntimeObservationService(session)
    project = _project()
    pod = _pod(status=PodStatus.PROVISIONING)
    svc.observe = AsyncMock(  # type: ignore[method-assign]
        return_value={"observed_state": ObservedState.RUNNING.value},
    )
    with patch("prodavan.application.pod_service.runtime_observation.settings") as mock_settings:
        mock_settings.pod_runtime_mode = "sandbox"
        mock_settings.pod_provisioning_timeout_sec = 300
        action = await svc.promote_or_demote(project=project, pod=pod)
    assert action == "promoted"
    assert pod.status == PodStatus.RUNNING
    assert pod.last_error is None


@pytest.mark.asyncio
async def test_promote_or_demote_sandbox_does_not_promote_on_transitional() -> None:
    # The claim is not Ready yet: transitional observations must not promote
    # (bind/register would race an un-adopted sandbox) — the pod stays
    # PROVISIONING until the observed state is `running`.
    session = MagicMock()
    svc = RuntimeObservationService(session)
    project = _project()
    pod = _pod(status=PodStatus.PROVISIONING)
    svc.observe = AsyncMock(  # type: ignore[method-assign]
        return_value={"observed_state": ObservedState.PROVISIONING.value},
    )
    with patch("prodavan.application.pod_service.runtime_observation.settings") as mock_settings:
        mock_settings.pod_runtime_mode = "sandbox"
        mock_settings.pod_provisioning_timeout_sec = 300
        action = await svc.promote_or_demote(project=project, pod=pod)
    assert action == "noop"
    assert pod.status == PodStatus.PROVISIONING


# ---------------------------------------------------- cold-start budget (B7)


@pytest.mark.asyncio
async def test_observe_sandbox_cold_provisioning_maps_to_pulling() -> None:
    """B7: warm-pool miss → cold sandbox pulls the image; report PULLING so
    promote_or_demote grants the image-pull budget."""
    session = AsyncMock()
    svc = RuntimeObservationService(session)
    project = _project()
    pod = _pod(status=PodStatus.PROVISIONING)

    runtime_mock = AsyncMock()
    runtime_mock.get_status = AsyncMock(
        return_value={
            "observed_state": "provisioning",
            "phase": "Pending",
            "ready": False,
            "launch_type": "cold",
            "sandbox_name": "sbx-cold1",
        }
    )

    with (
        patch("prodavan.application.pod_service.runtime_observation.settings") as mock_settings,
        patch(
            "prodavan.application.pod_service.runtime_observation.build_pod_runtime",
            return_value=runtime_mock,
        ),
    ):
        mock_settings.pod_runtime_mode = "sandbox"
        out = await svc.observe(project=project, pod=pod)

    assert out["observed_state"] == ObservedState.PULLING.value
    assert out["launch_type"] == "cold"


@pytest.mark.asyncio
async def test_promote_or_demote_sandbox_cold_start_within_pull_budget() -> None:
    """B7: sandbox cold launch at age 150s must NOT fail — the image-pull
    budget (600s) applies, not the provisioning budget (120s)."""
    session = MagicMock()
    svc = RuntimeObservationService(session)
    project = _project()
    pod = _pod(status=PodStatus.PROVISIONING)
    pod.updated_at = datetime.now(UTC) - timedelta(seconds=150)
    svc.observe = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "observed_state": ObservedState.PULLING.value,
            "launch_type": "cold",
        }
    )
    with patch("prodavan.application.pod_service.runtime_observation.settings") as mock_settings:
        mock_settings.pod_runtime_mode = "sandbox"
        mock_settings.pod_provisioning_timeout_sec = 120
        mock_settings.pod_image_pull_timeout_sec = 600
        action = await svc.promote_or_demote(project=project, pod=pod)
    assert action == "noop"
    assert pod.status == PodStatus.PROVISIONING
    assert project.status == ProjectStatus.ACTIVE


@pytest.mark.asyncio
async def test_promote_or_demote_sandbox_warm_provisioning_still_times_out() -> None:
    """Guard: only COLD launches get the image-pull budget — a stuck warm
    adoption still fails at the provisioning budget."""
    session = MagicMock()
    svc = RuntimeObservationService(session)
    project = _project()
    pod = _pod(status=PodStatus.PROVISIONING)
    pod.updated_at = datetime.now(UTC) - timedelta(seconds=150)
    svc.observe = AsyncMock(  # type: ignore[method-assign]
        return_value={"observed_state": ObservedState.PROVISIONING.value}
    )
    with patch("prodavan.application.pod_service.runtime_observation.settings") as mock_settings:
        mock_settings.pod_runtime_mode = "sandbox"
        mock_settings.pod_provisioning_timeout_sec = 120
        mock_settings.pod_image_pull_timeout_sec = 600
        action = await svc.promote_or_demote(project=project, pod=pod)
    assert action == "failed_timeout"
    assert pod.status == PodStatus.FAILED
