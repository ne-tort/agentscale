"""k8s PodRuntimePort adapter."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from prodavan.config.settings import settings
from prodavan.domain.pods.context import PodRuntimeContext
from prodavan.infrastructure.k8s.errors import K8sNotFoundError, PermanentK8sError
from prodavan.infrastructure.k8s.sandbox.client import K8sSandboxClient, PodSnapshot
from prodavan.infrastructure.k8s.sandbox.pod_spec import build_pod_body

logger = logging.getLogger(__name__)

_RUNNING_PHASES = frozenset({"Running", "Pending"})
_GONE_PHASES = frozenset({"Terminating", "Succeeded"})
_POD_START_ATTEMPTS = 3
_POD_START_RETRY_DELAY_SEC = 3.0


class K8sPodRuntimeAdapter:
    def __init__(self, *, client: K8sSandboxClient) -> None:
        self._client = client

    async def ensure_running(
        self,
        *,
        runtime_ref: str,
        context: PodRuntimeContext,
    ) -> None:
        last_exc: K8sNotFoundError | None = None
        for attempt in range(1, _POD_START_ATTEMPTS + 1):
            try:
                await self._ensure_running_once(runtime_ref=runtime_ref, context=context)
                return
            except K8sNotFoundError as exc:
                last_exc = exc
                if attempt >= _POD_START_ATTEMPTS:
                    raise
                logger.warning(
                    "k8s pod startup not-found retry runtime_ref=%s attempt=%s/%s",
                    runtime_ref,
                    attempt,
                    _POD_START_ATTEMPTS,
                )
                await asyncio.sleep(_POD_START_RETRY_DELAY_SEC)
        if last_exc is not None:
            raise last_exc

    async def _ensure_running_once(
        self,
        *,
        runtime_ref: str,
        context: PodRuntimeContext,
    ) -> None:
        existing = await self._client.get_pod(runtime_ref)
        if existing is not None:
            if existing.phase in _GONE_PHASES:
                await self._client.wait_absent(runtime_ref)
                existing = None
            elif self._needs_recreate(existing, context):
                logger.info(
                    "k8s pod recreate runtime_ref=%s generation=%s->%s",
                    runtime_ref,
                    existing.hydrate_generation,
                    context.hydrate_generation,
                )
                await self._delete_and_wait(runtime_ref)
                existing = None
            elif existing.phase in _RUNNING_PHASES:
                if existing.fatal_failure:
                    raise PermanentK8sError(
                        f"pod {runtime_ref} cannot start: {existing.fatal_failure}"
                    )
                # Do not block on Ready — observe/reconcile promotes RUNNING.
                return
            elif existing.phase == "Failed":
                await self._delete_and_wait(runtime_ref)
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
            minio_secret_name=None,
            agent_runtime_image=(
                settings.pod_agent_runtime_image if settings.pod_agent_runtime_enabled else None
            ),
            agent_runtime_port=settings.pod_agent_runtime_port,
            agent_runtime_api_base_url=settings.pod_agent_runtime_api_base_url,
            agent_runtime_auth_secret=settings.pod_agent_runtime_auth_secret or None,
            agent_runtime_web_search_provider=settings.pod_agent_runtime_web_search_provider,
            agent_runtime_web_search_url=settings.pod_agent_runtime_web_search_url,
            agent_runtime_web_search_api_key=settings.pod_agent_runtime_web_search_api_key,
            image_pull_secret=settings.pod_sandbox_image_pull_secret or None,
        )
        await self._client.create_pod(body)
        await self._client.wait_exists(runtime_ref)
        # Ready is observed asynchronously — keep launch HTTP short.

    async def _delete_and_wait(self, runtime_ref: str) -> None:
        try:
            await self._client.delete_pod(runtime_ref, grace_period=0)
        except K8sNotFoundError:
            return
        await self._client.wait_absent(runtime_ref)

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
        if existing.hydrate_generation != context.hydrate_generation:
            return True
        # Bridge token generation: pause/reload/terminate bump the pod bridge
        # generation after the pod was created. A pod carrying a stale bridge
        # token (gen < current) cannot hydrate and crashes Init with HTTP 401.
        # Recreate it so the freshly minted token (gen=current) is baked in.
        if context.pod_bridge_gen is not None:
            if existing.bridge_generation is None:
                return True
            if existing.bridge_generation != context.pod_bridge_gen:
                return True
        return False
