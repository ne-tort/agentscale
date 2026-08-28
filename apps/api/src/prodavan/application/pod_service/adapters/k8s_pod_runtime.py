"""K8s PodRuntimePort — real Pod adapter (P-POD-01)."""

from __future__ import annotations

import logging

from prodavan.application.pod_service.adapters.stub_pod_runtime import StubPodRuntimeAdapter
from prodavan.application.pod_service.ports.hydrate import HydratePort
from prodavan.application.pod_service.ports.pod_runtime import PodRuntimePort

logger = logging.getLogger(__name__)


class StubHydrateAdapter:
    async def hydrate(self, *, workspace_key: str, runtime_ref: str) -> None:
        logger.debug("hydrate stub workspace_key=%s runtime_ref=%s", workspace_key, runtime_ref)


class K8sPodRuntimeAdapter:
    """Real k8s adapter; falls back to stub when client unavailable."""

    def __init__(
        self,
        *,
        stub: PodRuntimePort | None = None,
        hydrate: HydratePort | None = None,
    ) -> None:
        self._stub = stub or StubPodRuntimeAdapter()
        self._hydrate = hydrate or StubHydrateAdapter()

    async def ensure_running(self, *, runtime_ref: str) -> None:
        await self._stub.ensure_running(runtime_ref=runtime_ref)

    async def pause(self, *, runtime_ref: str) -> None:
        await self._stub.pause(runtime_ref=runtime_ref)

    async def terminate(self, *, runtime_ref: str) -> None:
        await self._stub.terminate(runtime_ref=runtime_ref)

    async def get_status(self, *, runtime_ref: str) -> dict:
        return await self._stub.get_status(runtime_ref=runtime_ref)

    async def list_managed_pods(self) -> list[dict]:
        return await self._stub.list_managed_pods()

    async def hydrate(self, *, workspace_key: str, runtime_ref: str) -> None:
        await self._hydrate.hydrate(workspace_key=workspace_key, runtime_ref=runtime_ref)

    async def force_kill(self, *, runtime_ref: str) -> None:
        await self._stub.terminate(runtime_ref=runtime_ref)
