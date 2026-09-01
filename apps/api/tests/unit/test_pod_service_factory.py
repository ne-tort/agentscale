"""Unit tests — pod_service adapter factory wiring."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import prodavan.application.pod_service.factory as factory_mod
from prodavan.application.pod_service.adapters.k8s.workspace_exec import K8sExecWorkspaceAdapter
from prodavan.application.pod_service.adapters.k8s.workspace_http import HttpAgentRuntimeWorkspaceAdapter
from prodavan.application.pod_service.adapters.stub_pod_runtime import StubPodRuntimeAdapter
from prodavan.application.pod_service.adapters.unavailable_workspace import UnavailableWorkspaceAdapter


@pytest.fixture(autouse=True)
def _reset_factory_singletons() -> None:
    factory_mod._runtime_singleton = None
    factory_mod._metrics_singleton = None
    factory_mod._metrics_resolved = False
    yield
    factory_mod._runtime_singleton = None
    factory_mod._metrics_singleton = None
    factory_mod._metrics_resolved = False


def test_build_pod_runtime_stub_mode() -> None:
    with patch("prodavan.application.pod_service.factory.settings") as mock_settings:
        mock_settings.pod_runtime_mode = "stub"
        adapter = factory_mod.build_pod_runtime(force_new=True)
    assert isinstance(adapter, StubPodRuntimeAdapter)


def test_build_pod_workspace_stub_mode() -> None:
    with patch("prodavan.application.pod_service.factory.settings") as mock_settings:
        mock_settings.pod_runtime_mode = "stub"
        adapter = factory_mod.build_pod_workspace()
    assert isinstance(adapter, UnavailableWorkspaceAdapter)


def test_build_pod_workspace_k8s_uses_http_when_runtime_enabled() -> None:
    mock_client = MagicMock()
    mock_mgr = MagicMock()
    mock_mgr.client = mock_client

    with (
        patch("prodavan.application.pod_service.factory.settings") as mock_settings,
        patch("prodavan.core.infra.k8s_manager.get_k8s_manager", return_value=mock_mgr),
    ):
        mock_settings.pod_runtime_mode = "k8s"
        mock_settings.pod_agent_runtime_enabled = True
        adapter = factory_mod.build_pod_workspace()

    assert isinstance(adapter, HttpAgentRuntimeWorkspaceAdapter)
    assert adapter._client is mock_client


def test_build_pod_workspace_k8s_uses_exec_when_runtime_disabled() -> None:
    mock_client = MagicMock()
    mock_mgr = MagicMock()
    mock_mgr.client = mock_client

    with (
        patch("prodavan.application.pod_service.factory.settings") as mock_settings,
        patch("prodavan.core.infra.k8s_manager.get_k8s_manager", return_value=mock_mgr),
    ):
        mock_settings.pod_runtime_mode = "k8s"
        mock_settings.pod_agent_runtime_enabled = False
        adapter = factory_mod.build_pod_workspace()

    assert isinstance(adapter, K8sExecWorkspaceAdapter)
    assert adapter._client is mock_client


def test_build_pod_workspace_k8s_raises_without_client() -> None:
    with (
        patch("prodavan.application.pod_service.factory.settings") as mock_settings,
        patch("prodavan.core.infra.k8s_manager.get_k8s_manager", return_value=None),
    ):
        mock_settings.pod_runtime_mode = "k8s"
        with pytest.raises(RuntimeError, match="K8sManager client is unavailable"):
            factory_mod.build_pod_workspace()
