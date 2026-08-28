"""k8s PodRuntimePort adapter."""

from __future__ import annotations

import logging
from typing import Any

from prodavan.config.settings import settings
from prodavan.domain.pods.context import PodRuntimeContext
from prodavan.infrastructure.k8s.errors import K8sNotFoundError
from prodavan.infrastructure.k8s.sandbox.client import K8sSandboxClient, PodSnapshot
from prodavan.infrastructure.k8s.sandbox.pod_spec import build_pod_body

logger = logging.getLogger(__name__)

_RUNNING_PHASES = frozenset({"Running", "Pending"})


class K8sPodRuntimeAdapter:
    def __init__(self, *, client: K8sSandboxClient) -> None:
        self._client = client

    async def ensure_running(
        self,
        *,
        runtime_ref: str,
        context: PodRuntimeContext,
    ) -> None:
        existing = await self._client.get_pod(runtime_ref)
        if existing is not None:
            if self._needs_recreate(existing, context):
                logger.info(
                    "k8s pod recreate runtime_ref=%s generation=%s->%s",
                    runtime_ref,
                    existing.hydrate_generation,
                    context.hydrate_generation,
                )
                await self._client.delete_pod(runtime_ref, grace_period=0)
                existing = None
            elif existing.phase in _RUNNING_PHASES:
                await self._client.wait_ready(
                    runtime_ref,
                    timeout=float(settings.pod_ready_timeout_sec),
                )
                return
            elif existing.phase == "Failed":
                await self._client.delete_pod(runtime_ref, grace_period=0)
                existing = None

        body = build_pod_body(
            runtime_ref=runtime_ref,
            namespace=self._client.namespace,
            context=context,
            image=settings.pod_sandbox_image,
            hydrate_image=settings.pod_sandbox_hydrate_image,
            service_account=settings.pod_sandbox_sa,
            cpu_request=settings.pod_sandbox_cpu_request,
            cpu_limit=settings.pod_sandbox_cpu_limit,
            memory_request=settings.pod_sandbox_memory_request,
            memory_limit=settings.pod_sandbox_memory_limit,
            minio_secret_name=settings.pod_sandbox_minio_secret or None,
        )
        await self._client.create_pod(body)
        await self._client.wait_ready(runtime_ref, timeout=float(settings.pod_ready_timeout_sec))

    async def pause(self, *, runtime_ref: str) -> None:
        try:
            await self._client.delete_pod(runtime_ref, grace_period=30)
        except K8sNotFoundError:
            return

    async def terminate(self, *, runtime_ref: str) -> None:
        try:
            await self._client.delete_pod(runtime_ref, grace_period=0)
        except K8sNotFoundError:
            return

    async def force_kill(self, *, runtime_ref: str) -> None:
        await self.terminate(runtime_ref=runtime_ref)

    async def get_status(self, *, runtime_ref: str) -> dict[str, Any]:
        snap = await self._client.get_pod(runtime_ref)
        if snap is None:
            return {"runtime_ref": runtime_ref, "phase": "NotFound", "stub": False}
        return snap.as_status_dict()

    async def list_managed_pods(self) -> list[dict[str, Any]]:
        snaps = await self._client.list_managed_pods()
        out: list[dict[str, Any]] = []
        for snap in snaps:
            item = snap.as_status_dict()
            item["pod_id"] = snap.labels.get("prodavan.io/pod-id")
            item["project_id"] = snap.labels.get("prodavan.io/project-id")
            item["company_id"] = snap.labels.get("prodavan.io/company-id")
            item["workspace_key"] = snap.labels.get("prodavan.io/workspace-key")
            item["hydrate_generation"] = snap.hydrate_generation
            out.append(item)
        return out

    @staticmethod
    def _needs_recreate(existing: PodSnapshot, context: PodRuntimeContext) -> bool:
        if existing.phase not in _RUNNING_PHASES:
            return False
        if existing.hydrate_generation is None:
            return False
        return existing.hydrate_generation != context.hydrate_generation
