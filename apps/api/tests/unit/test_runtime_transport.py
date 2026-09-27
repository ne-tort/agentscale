"""runtime_transport unit tests — sandbox (router) vs k8s (pod-IP) resolution."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.agent import runtime_transport as rt
from prodavan.application.agent.runtime_transport import (
    RuntimeEndpoint,
    resolve_runtime_endpoint,
    resolve_runtime_endpoint_for_ref,
)

REF = "sandbox-claim-ws-demo-01"
ROUTER = "http://sandbox-router-svc.agent-sandbox-system.svc:8080"


def _mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setattr(rt.settings, "pod_runtime_mode", mode)
    monkeypatch.setattr(rt.settings, "pod_sandbox_router_url", ROUTER)
    monkeypatch.setattr(rt.settings, "pod_sandbox_namespace", "prodavan-sandboxes")
    monkeypatch.setattr(rt.settings, "pod_agent_runtime_port", 3921)


def _runtime_with_status(status: dict[str, Any] | None = None, *, raises: bool = False) -> Any:
    runtime = MagicMock()
    if raises:
        runtime.get_status = AsyncMock(side_effect=RuntimeError("sdk down"))
    else:
        runtime.get_status = AsyncMock(return_value=status or {})
    return runtime


@pytest.mark.asyncio
async def test_sandbox_endpoint_uses_router_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    _mode(monkeypatch, "sandbox")
    runtime = _runtime_with_status({"sandbox_name": "sbx-abc123"})
    ep = await resolve_runtime_endpoint_for_ref(REF, runtime=runtime)
    assert ep is not None
    assert ep.base_url == ROUTER
    assert ep.headers == {
        "X-Sandbox-Id": "sbx-abc123",
        "X-Sandbox-Namespace": "prodavan-sandboxes",
        "X-Sandbox-Port": "3921",
    }


@pytest.mark.asyncio
async def test_sandbox_endpoint_none_until_adopted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Claim without status.sandbox.name (warm adoption pending) is not routable."""
    _mode(monkeypatch, "sandbox")
    runtime = _runtime_with_status({"sandbox_name": ""})
    assert await resolve_runtime_endpoint_for_ref(REF, runtime=runtime) is None


@pytest.mark.asyncio
async def test_sandbox_endpoint_none_on_status_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _mode(monkeypatch, "sandbox")
    runtime = _runtime_with_status(raises=True)
    assert await resolve_runtime_endpoint_for_ref(REF, runtime=runtime) is None


@pytest.mark.asyncio
async def test_sandbox_endpoint_none_without_router_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _mode(monkeypatch, "sandbox")
    monkeypatch.setattr(rt.settings, "pod_sandbox_router_url", "")
    runtime = _runtime_with_status({"sandbox_name": "sbx-abc123"})
    assert await resolve_runtime_endpoint_for_ref(REF, runtime=runtime) is None


@pytest.mark.asyncio
async def test_k8s_endpoint_direct_pod_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    _mode(monkeypatch, "k8s")
    snap = MagicMock(ready=True, phase="Running", pod_ip="10.42.0.88")
    client = MagicMock()
    client.available = MagicMock(return_value=True)
    client.get_pod = AsyncMock(return_value=snap)
    ep = await resolve_runtime_endpoint_for_ref("pod-ws-demo-01", k8s_client=client)
    assert ep is not None
    assert ep.base_url == "http://10.42.0.88:3921"
    assert ep.headers == {}


@pytest.mark.asyncio
async def test_k8s_endpoint_none_when_pod_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    _mode(monkeypatch, "k8s")
    snap = MagicMock(ready=False, phase="Pending", pod_ip=None)
    client = MagicMock()
    client.available = MagicMock(return_value=True)
    client.get_pod = AsyncMock(return_value=snap)
    assert await resolve_runtime_endpoint_for_ref("pod-ws-demo-01", k8s_client=client) is None


@pytest.mark.asyncio
async def test_stub_mode_has_no_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    _mode(monkeypatch, "stub")
    assert await resolve_runtime_endpoint_for_ref("object-ws:demo", runtime=None) is None


@pytest.mark.asyncio
async def test_resolve_runtime_endpoint_filters_non_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB gate: observed_state != running → no endpoint (no traffic to dead pods)."""
    _mode(monkeypatch, "sandbox")
    view = {"observed_state": "provisioning", "runtime_ref": REF}

    async def _fake_runtime_view(self: Any, project_id: str) -> dict[str, Any]:
        return view

    monkeypatch.setattr(
        "prodavan.application.pod_service.query.PodQuery.runtime_view", _fake_runtime_view
    )
    assert await resolve_runtime_endpoint(MagicMock(), "prj_1") is None

    view["observed_state"] = "running"
    runtime = _runtime_with_status({"sandbox_name": "sbx-abc123"})
    ep = await resolve_runtime_endpoint(MagicMock(), "prj_1", runtime=runtime)
    assert ep is not None and ep.headers["X-Sandbox-Id"] == "sbx-abc123"


@pytest.mark.asyncio
async def test_resolve_runtime_endpoint_skips_stub_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    _mode(monkeypatch, "sandbox")
    view = {"observed_state": "running", "runtime_ref": "object-ws:demo"}

    async def _fake_runtime_view(self: Any, project_id: str) -> dict[str, Any]:
        return view

    monkeypatch.setattr(
        "prodavan.application.pod_service.query.PodQuery.runtime_view", _fake_runtime_view
    )
    assert await resolve_runtime_endpoint(MagicMock(), "prj_1") is None
