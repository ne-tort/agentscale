"""PodRuntimePort — k8s Pod abstraction (module 14)."""

from __future__ import annotations

from typing import Any, Protocol

from prodavan.domain.pods.context import PodRuntimeContext


class PodRuntimePort(Protocol):
    async def ensure_running(
        self,
        *,
        runtime_ref: str,
        context: PodRuntimeContext,
    ) -> None: ...

    async def pause(self, *, runtime_ref: str) -> None: ...

    async def terminate(self, *, runtime_ref: str) -> None: ...

    async def force_kill(self, *, runtime_ref: str) -> None: ...

    async def get_status(self, *, runtime_ref: str) -> dict[str, Any]: ...

    async def list_managed_pods(self) -> list[dict[str, Any]]: ...
