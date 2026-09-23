"""Unit tests — K8sPodRuntimeAdapter._needs_recreate bridge-generation gate."""

from __future__ import annotations

from prodavan.application.pod_service.adapters.k8s.pod_runtime import K8sPodRuntimeAdapter
from prodavan.domain.pods.context import PodRuntimeContext
from prodavan.infrastructure.k8s.sandbox.client import PodSnapshot


def _snap(*, phase: str = "Pending", hydrate_gen: int | None = 0, bridge_gen: int | None = None) -> PodSnapshot:
    return PodSnapshot(
        name="pod-test",
        uid="uid",
        phase=phase,
        restarts=0,
        ready=False,
        labels={},
        hydrate_generation=hydrate_gen,
        bridge_generation=bridge_gen,
    )


def _ctx(*, hydrate_gen: int = 0, bridge_gen: int | None = None) -> PodRuntimeContext:
    return PodRuntimeContext(
        pod_id="pod_test",
        project_id="proj_test",
        company_id="co_test",
        workspace_key="ws",
        hydrate_generation=hydrate_gen,
        pod_bridge_gen=bridge_gen,
    )


def test_recreate_when_bridge_gen_stale() -> None:
    existing = _snap(phase="Pending", hydrate_gen=0, bridge_gen=0)
    context = _ctx(hydrate_gen=0, bridge_gen=1)
    assert K8sPodRuntimeAdapter._needs_recreate(existing, context) is True


def test_no_recreate_when_bridge_gen_matches() -> None:
    existing = _snap(phase="Pending", hydrate_gen=0, bridge_gen=1)
    context = _ctx(hydrate_gen=0, bridge_gen=1)
    assert K8sPodRuntimeAdapter._needs_recreate(existing, context) is False


def test_recreate_when_existing_bridge_gen_missing() -> None:
    existing = _snap(phase="Pending", hydrate_gen=0, bridge_gen=None)
    context = _ctx(hydrate_gen=0, bridge_gen=1)
    assert K8sPodRuntimeAdapter._needs_recreate(existing, context) is True


def test_recreate_when_hydrate_gen_changes() -> None:
    existing = _snap(phase="Running", hydrate_gen=0, bridge_gen=1)
    context = _ctx(hydrate_gen=1, bridge_gen=1)
    assert K8sPodRuntimeAdapter._needs_recreate(existing, context) is True


def test_no_recreate_for_failed_phase() -> None:
    existing = _snap(phase="Failed", hydrate_gen=0, bridge_gen=0)
    context = _ctx(hydrate_gen=0, bridge_gen=1)
    assert K8sPodRuntimeAdapter._needs_recreate(existing, context) is False