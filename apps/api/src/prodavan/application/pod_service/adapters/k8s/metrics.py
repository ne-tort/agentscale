"""k8s metrics-server adapter."""

from __future__ import annotations

from typing import Any

from prodavan.infrastructure.k8s.sandbox.client import K8sSandboxClient


class K8sPodMetricsAdapter:
    def __init__(self, *, client: K8sSandboxClient) -> None:
        self._client = client

    async def get_pod_metrics(
        self, *, runtime_ref: str, sandbox_name: str | None = None
    ) -> dict[str, Any] | None:
        # sandbox_name is a sandbox-mode hint; in k8s mode runtime_ref is
        # already the pod name.
        return await self._client.get_pod_metrics(runtime_ref)
