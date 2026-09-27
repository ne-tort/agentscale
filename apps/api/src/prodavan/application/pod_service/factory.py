"""Adapter factory — single wiring point for pod_service runtime."""

from __future__ import annotations

import logging

from prodavan.application.pod_service.adapters.stub_hydrate import StubHydrateAdapter
from prodavan.application.pod_service.adapters.stub_pod_runtime import StubPodRuntimeAdapter
from prodavan.application.pod_service.ports.hydrate import HydratePort
from prodavan.application.pod_service.ports.pod_metrics import PodMetricsPort
from prodavan.application.pod_service.ports.pod_runtime import PodRuntimePort
from prodavan.config.settings import settings

logger = logging.getLogger(__name__)

_runtime_singleton: PodRuntimePort | None = None
_metrics_singleton: PodMetricsPort | None = None
_metrics_resolved: bool = False


def build_pod_runtime(*, force_new: bool = False) -> PodRuntimePort:
    global _runtime_singleton
    if not force_new and _runtime_singleton is not None:
        return _runtime_singleton
    mode = (settings.pod_runtime_mode or "stub").strip().lower()
    if mode == "k8s":
        from prodavan.application.pod_service.adapters.k8s.pod_runtime import K8sPodRuntimeAdapter
        from prodavan.core.infra.k8s_manager import get_k8s_manager

        mgr = get_k8s_manager()
        if mgr is None or mgr.client is None:
            raise RuntimeError("pod_runtime_mode=k8s but K8sManager client is unavailable")
        logger.info("pod_runtime: k8s adapter namespace=%s", mgr.client.namespace)
        adapter: PodRuntimePort = K8sPodRuntimeAdapter(client=mgr.client)
    elif mode == "sandbox":
        from prodavan.application.pod_service.adapters.agent_sandbox import (
            AgentSandboxPodRuntimeAdapter,
        )

        # SDK client is resolved lazily from the SandboxClientResource so the
        # singleton survives resource restarts; mode errors surface on first use.
        adapter = AgentSandboxPodRuntimeAdapter()
        logger.info(
            "pod_runtime: agent-sandbox adapter namespace=%s warmpool=%s",
            settings.pod_sandbox_namespace,
            settings.pod_sandbox_warmpool,
        )
    else:
        adapter = StubPodRuntimeAdapter()
    if not force_new:
        _runtime_singleton = adapter
    return adapter


def build_hydrate() -> HydratePort:
    mode = (settings.pod_runtime_mode or "stub").strip().lower()
    if mode == "k8s":
        from prodavan.application.pod_service.adapters.k8s.hydrate_init import K8sInitHydrateAdapter

        return K8sInitHydrateAdapter()
    # sandbox mode: workspace lives on the claim PVC — no hydrate step.
    return StubHydrateAdapter()


def build_pod_metrics() -> PodMetricsPort | None:
    global _metrics_singleton, _metrics_resolved
    if _metrics_resolved:
        return _metrics_singleton
    _metrics_resolved = True
    mode = (settings.pod_runtime_mode or "stub").strip().lower()
    if mode != "k8s":
        return None
    from prodavan.application.pod_service.adapters.k8s.metrics import K8sPodMetricsAdapter
    from prodavan.core.infra.k8s_manager import get_k8s_manager

    mgr = get_k8s_manager()
    if mgr is None or mgr.client is None:
        return None
    _metrics_singleton = K8sPodMetricsAdapter(client=mgr.client)
    return _metrics_singleton


def build_dehydrate():
    mode = (settings.pod_runtime_mode or "stub").strip().lower()
    if mode == "k8s":
        from prodavan.application.pod_service.adapters.k8s.dehydrate import K8sDehydrateAdapter
        from prodavan.core.infra.k8s_manager import get_k8s_manager

        mgr = get_k8s_manager()
        if mgr is None or mgr.client is None:
            raise RuntimeError("pod_runtime_mode=k8s but K8sManager client is unavailable")
        return K8sDehydrateAdapter(client=mgr.client)
    from prodavan.application.pod_service.adapters.stub_dehydrate import StubDehydrateAdapter

    # sandbox mode falls through too: PVC is the source of truth, no
    # Pod-FS -> object-store sync is needed (Wave 2 gap: intentional).
    return StubDehydrateAdapter()


def build_pod_workspace():
    mode = (settings.pod_runtime_mode or "stub").strip().lower()
    if mode == "k8s":
        from prodavan.core.infra.k8s_manager import get_k8s_manager

        mgr = get_k8s_manager()
        if mgr is None or mgr.client is None:
            raise RuntimeError("pod_runtime_mode=k8s but K8sManager client is unavailable")
        if settings.pod_agent_runtime_enabled:
            from prodavan.application.pod_service.adapters.k8s.workspace_http import (
                HttpAgentRuntimeWorkspaceAdapter,
            )

            return HttpAgentRuntimeWorkspaceAdapter(client=mgr.client)
        from prodavan.application.pod_service.adapters.k8s.workspace_exec import K8sExecWorkspaceAdapter

        return K8sExecWorkspaceAdapter(client=mgr.client)
    from prodavan.application.pod_service.adapters.unavailable_workspace import UnavailableWorkspaceAdapter

    return UnavailableWorkspaceAdapter()
