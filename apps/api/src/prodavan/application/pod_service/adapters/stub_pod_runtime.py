"""Stub PodRuntimePort — object-ws refs (no real compute)."""

from __future__ import annotations

from typing import Any

from prodavan.domain.pods.context import PodRuntimeContext


class StubPodRuntimeAdapter:
    """No-op runtime: DB row + events only; no k8s or object-ws side effects."""

    async def ensure_running(
        self,
        *,
        runtime_ref: str,
        context: PodRuntimeContext,
    ) -> None:
        return None

    async def pause(self, *, runtime_ref: str) -> None:
        return None

    async def terminate(self, *, runtime_ref: str) -> None:
        return None

    async def force_kill(self, *, runtime_ref: str) -> None:
        return None

    async def get_status(self, *, runtime_ref: str) -> dict[str, Any]:
        return {"runtime_ref": runtime_ref, "phase": "Unknown", "stub": True, "ready": False}

    async def list_managed_pods(self) -> list[dict[str, Any]]:
        return []
