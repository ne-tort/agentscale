"""Sandbox-mode PodMetricsPort: claim ref → Sandbox (pod) name → metrics-server.

In ``pod_runtime_mode=sandbox`` the workload identity is the SandboxClaim
(``runtime_ref``), while metrics-server indexes usage by *pod name* — which
for agent-sandbox equals the Sandbox name (``claim.status.sandbox.name``).
The adapter resolves the mapping (callers holding a fresh status may pass
``sandbox_name`` directly to skip the claim GET) and queries metrics.k8s.io
in the sandbox namespace via the lightweight in-cluster REST client.

RBAC: ``metrics.k8s.io pods get/list`` for SA prodavan-api is granted by
``infra/k3s/base/prodavan-sandboxes-ks/rbac-sandboxes.yaml``.

Suspended/absent sandboxes have no pod → metrics-server 404 → ``None``
(caller reports metrics_available=false; this is honest, not an error).
"""

from __future__ import annotations

import logging
from typing import Any

from prodavan.config.settings import settings
from prodavan.infrastructure.k8s.sandbox.client import K8sSandboxClient

logger = logging.getLogger(__name__)


class SandboxPodMetricsAdapter:
    """PodMetricsPort over metrics.k8s.io for agent-sandbox pods."""

    def __init__(
        self,
        *,
        client: K8sSandboxClient | None = None,
        runtime: Any | None = None,
    ) -> None:
        # ``client``: injectable for tests; production resolves lazily so the
        # singleton survives env/auth reloads. ``runtime``: PodRuntimePort used
        # to resolve claim→sandbox name when the caller has no fresh status.
        self._client = client
        self._runtime = runtime

    def _k8s(self) -> K8sSandboxClient:
        if self._client is None:
            self._client = K8sSandboxClient(namespace=settings.pod_sandbox_namespace)
        return self._client

    async def _resolve_sandbox_name(self, runtime_ref: str) -> str | None:
        runtime = self._runtime
        if runtime is None:
            from prodavan.application.pod_service.factory import build_pod_runtime

            try:
                runtime = build_pod_runtime()
            except Exception as exc:
                logger.warning("sandbox metrics: runtime unavailable: %s", exc)
                return None
        try:
            status = await runtime.get_status(runtime_ref=runtime_ref)
        except Exception as exc:
            logger.warning(
                "sandbox metrics: status failed runtime_ref=%s: %s", runtime_ref, exc
            )
            return None
        name = str((status or {}).get("sandbox_name") or "").strip()
        return name or None

    async def get_pod_metrics(
        self, *, runtime_ref: str, sandbox_name: str | None = None
    ) -> dict[str, Any] | None:
        name = (sandbox_name or "").strip() or await self._resolve_sandbox_name(runtime_ref)
        if not name:
            return None
        client = self._k8s()
        if not client.available():
            return None
        try:
            return await client.get_pod_metrics(name)
        except Exception as exc:
            logger.warning("sandbox metrics: query failed sandbox=%s: %s", name, exc)
            return None
