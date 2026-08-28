"""Stub ContainerRuntimePort — object-ws refs (as-built)."""

from __future__ import annotations

from prodavan.application.projects.container_lifecycle import (
    ensure_container_running,
    pause_container,
)


class StubContainerRuntimeAdapter:
    async def ensure_running(self, *, runtime_ref: str) -> None:
        await ensure_container_running(container_ref=runtime_ref)

    async def pause(self, *, runtime_ref: str) -> None:
        await pause_container(container_ref=runtime_ref)

    async def terminate(self, *, runtime_ref: str) -> None:
        await pause_container(container_ref=runtime_ref)
