"""ContainerRuntimePort — k8s Pod abstraction (module 14)."""

from __future__ import annotations

from typing import Protocol


class ContainerRuntimePort(Protocol):
    async def ensure_running(self, *, runtime_ref: str) -> None: ...

    async def pause(self, *, runtime_ref: str) -> None: ...

    async def terminate(self, *, runtime_ref: str) -> None: ...
